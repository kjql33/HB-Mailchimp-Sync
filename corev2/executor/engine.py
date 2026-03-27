"""
Sync Executor - applies operations from operations_plan.json.

Features:
- Idempotent operations (add/remove tags succeed if already applied)
- Rate limiting + retry with exponential backoff
- Operation journal (JSONL format)
- Dry-run mode (simulates without mutations)
- Stops on first dangerous failure
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from corev2.config.schema import V2Config
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mailchimp_client import MailchimpClient

logger = logging.getLogger(__name__)


class OperationJournal:
    """JSONL journal for tracking operation execution."""
    
    def __init__(self, journal_path: Path):
        """
        Initialize journal.
        
        Args:
            journal_path: Path to journal file (.jsonl)
        """
        self.journal_path = journal_path
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Open in append mode
        self.file = open(journal_path, 'a', encoding='utf-8')
    
    def log(self, entry: Dict[str, Any]):
        """
        Write entry to journal.
        
        Args:
            entry: Journal entry dict
        """
        entry["timestamp"] = datetime.utcnow().isoformat()
        self.file.write(json.dumps(entry) + "\n")
        self.file.flush()
    
    def close(self):
        """Close journal file."""
        self.file.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


class SyncExecutor:
    """Executes operations from operations_plan.json."""
    
    def __init__(
        self,
        config: V2Config,
        hs_client: HubSpotClient,
        mc_client: MailchimpClient,
        dry_run: bool = False
    ):
        """
        Initialize executor.
        
        Args:
            config: Validated V2Config
            hs_client: HubSpot API client
            mc_client: Mailchimp API client
            dry_run: If True, simulate without mutations
        """
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        self.dry_run = dry_run
    
    async def execute_plan(
        self,
        plan: Dict[str, Any],
        journal_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Execute operations from plan.
        
        Args:
            plan: operations_plan.json dict
            journal_path: Optional path to journal file
        
        Returns:
            Execution summary with success/failure counts
        """
        if journal_path is None:
            journal_path = Path("corev2/artifacts/execution_journal.jsonl")
        
        logger.info(f"Starting execution (dry_run={self.dry_run})...")
        logger.info(f"Journal: {journal_path}")
        
        summary = {
            "total_operations": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "contacts_processed": 0,
            "dry_run": self.dry_run,
            "started_at": datetime.utcnow().isoformat(),
        }
        
        with OperationJournal(journal_path) as journal:
            # Log plan metadata
            journal.log({
                "event": "execution_started",
                "plan_metadata": plan.get("metadata", {}),
                "dry_run": self.dry_run
            })
            
            operations_list = plan.get("operations", [])
            logger.info(f"Processing {len(operations_list)} contacts...")
            
            for contact_ops in operations_list:
                email = contact_ops.get("email")
                vid = contact_ops.get("vid")
                ops = contact_ops.get("operations", [])
                
                logger.info(f"Processing contact: {email} (VID: {vid})")
                summary["contacts_processed"] += 1
                
                try:
                    for op in ops:
                        summary["total_operations"] += 1
                        op_type = op.get("type")
                        
                        logger.debug(f"  Executing {op_type}...")
                        
                        result = await self._execute_operation(op, journal)
                        
                        if result["success"]:
                            summary["successful"] += 1
                        elif result["skipped"]:
                            summary["skipped"] += 1
                        else:
                            summary["failed"] += 1
                            
                            # Check if this is a dangerous failure
                            if result.get("dangerous"):
                                logger.error(f"DANGEROUS FAILURE on {email}: {result['error']}")
                                journal.log({
                                    "event": "execution_stopped",
                                    "reason": "dangerous_failure",
                                    "contact": email,
                                    "operation": op,
                                    "error": result["error"]
                                })
                                summary["ended_at"] = datetime.utcnow().isoformat()
                                summary["stopped_reason"] = "dangerous_failure"
                                return summary
                
                except Exception as e:
                    logger.error(f"Unexpected error processing {email}: {e}")
                    summary["failed"] += 1
                    journal.log({
                        "event": "contact_error",
                        "email": email,
                        "error": str(e)
                    })
            
            journal.log({
                "event": "execution_completed",
                "summary": summary
            })
        
        summary["ended_at"] = datetime.utcnow().isoformat()
        logger.info(f"Execution complete: {summary['successful']} successful, "
                   f"{summary['failed']} failed, {summary['skipped']} skipped")
        
        return summary
    
    async def _execute_operation(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """
        Execute single operation.
        
        Args:
            op: Operation dict
            journal: Operation journal
        
        Returns:
            Result dict with success/failure info
        """
        op_type = op.get("type")
        
        try:
            if op_type == "upsert_mc_member":
                return await self._execute_upsert_mc_member(op, journal)
            
            elif op_type == "apply_mc_tag":
                return await self._execute_apply_mc_tag(op, journal)
            
            elif op_type == "remove_mc_tag":
                return await self._execute_remove_mc_tag(op, journal)
            
            elif op_type == "unsubscribe_mc_member":
                return await self._execute_unsubscribe_mc_member(op, journal)
            
            elif op_type == "archive_mc_member":
                return await self._execute_archive_mc_member(op, journal)
            
            elif op_type == "update_hs_property":
                return await self._execute_update_hs_property(op, journal)
            
            elif op_type == "add_hs_to_list":
                return await self._execute_add_hs_to_list(op, journal)
            
            elif op_type == "remove_hs_from_list":
                return await self._execute_remove_hs_from_list(op, journal)
            
            else:
                logger.warning(f"Unknown operation type: {op_type}")
                journal.log({
                    "event": "operation_skipped",
                    "operation": op,
                    "reason": "unknown_type"
                })
                return {"success": False, "skipped": True, "error": f"Unknown type: {op_type}"}
        
        except Exception as e:
            logger.error(f"Operation failed: {op_type} - {e}")
            journal.log({
                "event": "operation_failed",
                "operation": op,
                "error": str(e),
                "dangerous": False
            })
            return {"success": False, "skipped": False, "error": str(e), "dangerous": False}
    
    async def _execute_upsert_mc_member(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute upsert_mc_member operation."""
        email = op.get("email")
        merge_fields = op.get("merge_fields", {})
        status_if_new = op.get("status_if_new", "subscribed")
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "upsert_mc_member",
                "email": email,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            result = await self.mc_client.upsert_member(email, merge_fields, status_if_new)
            
            # If contact was restored from archive, clean up old tags
            if result.get('action') == 'restored_from_archive':
                logger.info(f"  Contact {email} restored from archive - cleaning old tags")
                
                # Get current tags
                member = await self.mc_client.get_member(email)
                current_tags = member.get('tags', [])
                
                # Remove ALL existing tags if any
                if current_tags:
                    logger.info(f"  Removing {len(current_tags)} old tags: {', '.join(current_tags)}")
                    remove_result = await self.mc_client.remove_tags(email, current_tags)
                    
                    if remove_result['success']:
                        logger.info(f"  ✓ Cleaned {len(current_tags)} old tags from {email}")
                        journal.log({
                            "event": "tags_cleaned_after_unarchive",
                            "email": email,
                            "tags_removed": current_tags
                        })
                    else:
                        logger.warning(f"  ✗ Failed to remove old tags from {email}")
                else:
                    logger.info(f"  No old tags to clean for {email}")
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "upsert_mc_member",
                "email": email,
                "result": result
            })
            
            return {"success": True, "skipped": False}
        
        except Exception as e:
            error_msg = str(e)
            
            # Check if this is a compliance error (expected)
            if "Member In Compliance State" in error_msg or "compliance state" in error_msg.lower():
                logger.warning(f"Skipping {email}: in compliance state (unsubscribed/bounced)")
                journal.log({
                    "event": "operation_skipped",
                    "operation_type": "upsert_mc_member",
                    "email": email,
                    "reason": "compliance_state"
                })
                return {"success": False, "skipped": True, "error": error_msg}
            
            # Other errors - log and raise
            logger.error(f"Upsert failed for {email}: {e}")
            raise
    
    async def _execute_apply_mc_tag(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute apply_mc_tag operation (idempotent)."""
        email = op.get("email")
        tag = op.get("tag")
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "apply_mc_tag",
                "email": email,
                "tag": tag,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            result = await self.mc_client.add_tags(email, [tag])
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "apply_mc_tag",
                "email": email,
                "tag": tag,
                "result": result
            })
            
            return {"success": True, "skipped": False}
        
        except Exception as e:
            logger.error(f"Tag add failed for {email}/{tag}: {e}")
            raise
    
    async def _execute_remove_mc_tag(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute remove_mc_tag operation (idempotent)."""
        email = op.get("email")
        
        # Support both single tag (legacy) and tags list (new)
        if "tags" in op:
            tags = op["tags"]
        elif "tag" in op:
            tags = [op["tag"]]
        else:
            tags = []
        
        if not tags:
            return {"success": True, "skipped": True}
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "remove_mc_tag",
                "email": email,
                "tags": tags,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            result = await self.mc_client.remove_tags(email, tags)
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "remove_mc_tag",
                "email": email,
                "tags": tags,
                "result": result
            })
            
            return {"success": True, "skipped": False}
        
        except Exception as e:
            logger.error(f"Tag remove failed for {email}/{tags}: {e}")
            raise
    
    async def _execute_unsubscribe_mc_member(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute unsubscribe_mc_member operation (sets status=unsubscribed in Mailchimp)."""
        email = op.get("email")
        reason = op.get("reason", "opted_out_in_hubspot")
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "unsubscribe_mc_member",
                "email": email,
                "reason": reason,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            logger.info(f"  Unsubscribing {email} in Mailchimp (reason: {reason})...")
            result = await self.mc_client.unsubscribe_member(email)
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "unsubscribe_mc_member",
                "email": email,
                "reason": reason,
                "result": result
            })
            
            logger.info(f"  ✓ Unsubscribed {email}")
            return {"success": True, "skipped": False}
        
        except Exception as e:
            # Handle case where member is already unsubscribed
            if "already" in str(e).lower() or "compliance" in str(e).lower():
                logger.debug(f"  {email} already unsubscribed")
                journal.log({
                    "event": "operation_skipped",
                    "operation_type": "unsubscribe_mc_member",
                    "email": email,
                    "reason": "already_unsubscribed"
                })
                return {"success": True, "skipped": True}
            
            logger.error(f"Unsubscribe failed for {email}: {e}")
            raise
    
    async def _execute_archive_mc_member(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute archive_mc_member operation (idempotent - 404=success)."""
        email = op.get("email")
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "archive_mc_member",
                "email": email,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        # Check if archival is allowed
        if not self.config.safety.allow_archive:
            logger.warning(f"Archival disabled - skipping archive for {email}")
            journal.log({
                "event": "operation_skipped",
                "operation_type": "archive_mc_member",
                "email": email,
                "reason": "archival_disabled"
            })
            return {"success": False, "skipped": True, "error": "Archival disabled"}
        
        try:
            logger.info(f"  Archiving {email} in Mailchimp...")
            result = await self.mc_client.archive_member(email)
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "archive_mc_member",
                "email": email,
                "result": result
            })
            
            logger.info(f"  ✓ Archived {email}")
            return {"success": True, "skipped": False}
        
        except Exception as e:
            logger.error(f"Archive failed for {email}: {e}")
            raise
    
    async def _execute_update_hs_property(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """Execute update_hs_property operation."""
        vid = op.get("vid")
        property_name = op.get("property")
        value = op.get("value")
        
        if self.dry_run:
            journal.log({
                "event": "operation_simulated",
                "operation_type": "update_hs_property",
                "vid": vid,
                "property": property_name,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            result = await self.hs_client.update_contact_property(vid, property_name, value)
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "update_hs_property",
                "vid": vid,
                "property": property_name,
                "result": result
            })
            
            return {"success": True, "skipped": False}
        
        except Exception as e:
            logger.error(f"Property update failed for VID {vid}: {e}")
            raise
    
    async def _execute_add_hs_to_list(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """
        Execute HubSpot list addition operation (secondary sync).

        Adds contact to a HubSpot list (used for handover/destination lists).

        Args:
            op: {
                "type": "add_hs_to_list",
                "list_id": str,
                "vid": int,
                "email": str,
                "reason": str
            }
            journal: Operation journal

        Returns:
            Result dict
        """
        list_id = op["list_id"]
        vid = op["vid"]
        email = op.get("email", "unknown")
        reason = op.get("reason", "unknown")

        logger.info(f"  Adding contact {email} (VID {vid}) to HubSpot List {list_id} (reason: {reason})")

        # Check run mode (simulation)
        if self.config.safety.run_mode == "dry-run":
            journal.log({
                "event": "operation_simulated",
                "operation_type": "add_hs_to_list",
                "list_id": list_id,
                "vid": vid,
                "email": email,
                "reason": reason,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}

        try:
            result = await self.hs_client.add_contact_to_list(list_id, vid)

            journal.log({
                "event": "operation_executed",
                "operation_type": "add_hs_to_list",
                "list_id": list_id,
                "vid": vid,
                "email": email,
                "reason": reason,
                "result": result
            })

            logger.info(f"  \u2713 Added {email} (VID {vid}) to HubSpot List {list_id}")
            return {"success": True, "skipped": False}

        except Exception as e:
            # Handle "already in list" as success (idempotent)
            error_str = str(e).lower()
            if "already" in error_str or "existing" in error_str:
                logger.debug(f"  Contact {email} (VID {vid}) already in List {list_id}")
                journal.log({
                    "event": "operation_skipped",
                    "operation_type": "add_hs_to_list",
                    "list_id": list_id,
                    "vid": vid,
                    "email": email,
                    "reason": "already_in_list"
                })
                return {"success": True, "skipped": True}

            # Other errors - log and raise
            logger.error(f"HubSpot list addition failed for {email} (VID {vid}) to List {list_id}: {e}")
            raise

    async def _execute_remove_hs_from_list(
        self,
        op: Dict[str, Any],
        journal: OperationJournal
    ) -> Dict[str, Any]:
        """
        Execute HubSpot list removal operation.
        
        Removes contact from HubSpot list (used for exclusion cleanup).
        
        Args:
            op: {
                "type": "remove_hs_from_list",
                "list_id": str,
                "vid": int,
                "reason": str
            }
            journal: Operation journal
        
        Returns:
            Result dict
        """
        list_id = op["list_id"]
        vid = op["vid"]
        reason = op.get("reason", "unknown")
        
        logger.info(f"  Removing contact VID {vid} from HubSpot List {list_id} (reason: {reason})")
        
        # Check run mode (simulation)
        if self.config.safety.run_mode == "dry-run":
            journal.log({
                "event": "operation_simulated",
                "operation_type": "remove_hs_from_list",
                "list_id": list_id,
                "vid": vid,
                "reason": reason,
                "dry_run": True
            })
            return {"success": True, "skipped": False, "simulated": True}
        
        try:
            result = await self.hs_client.remove_contact_from_list(list_id, vid)
            
            journal.log({
                "event": "operation_executed",
                "operation_type": "remove_hs_from_list",
                "list_id": list_id,
                "vid": vid,
                "reason": reason,
                "result": result
            })
            
            logger.info(f"  ✓ Removed VID {vid} from HubSpot List {list_id}")
            return {"success": True, "skipped": False}
        
        except Exception as e:
            # Handle 404: contact already removed (e.g., by HubSpot compliance auto-removal)
            if "404" in str(e):
                logger.debug(f"  Contact VID {vid} not in List {list_id} (already removed)")
                journal.log({
                    "event": "operation_skipped",
                    "operation_type": "remove_hs_from_list",
                    "list_id": list_id,
                    "vid": vid,
                    "reason": "already_removed",
                    "note": "404 - contact not in list"
                })
                return {"success": True, "skipped": True}
            
            # Other errors - log and raise
            logger.error(f"HubSpot list removal failed for VID {vid} from List {list_id}: {e}")
            raise
