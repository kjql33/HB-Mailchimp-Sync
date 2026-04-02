"""
Sync Executor - applies operations from operations_plan.json.

Features:
- Idempotent operations (add/remove tags succeed if already applied)
- Rate limiting + retry with exponential backoff
- Operation journal (JSONL format)
- Dry-run mode (simulates without mutations)
- Stops on first dangerous failure
- Audience cap enforcement with live re-checks
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


class AudienceCapGuard:
    """
    Enforces a hard cap on Mailchimp subscribed members.

    - Fetches live count at start and every `recheck_interval` new subscribes.
    - Tracks new subscribes (upsert where action=created or restored_from_archive).
    - Returns cap_reached=True when the audience would exceed the cap.
    - Sends Teams alert the moment the cap is hit.
    """

    def __init__(self, mc_client: MailchimpClient, cap: int, recheck_interval: int = 10):
        self.mc_client = mc_client
        self.cap = cap
        self.recheck_interval = recheck_interval
        self.enabled = cap > 0

        # State
        self.live_count: int = 0          # last known subscribed count from API
        self.new_subscribes: int = 0      # new members added THIS run
        self.cap_reached: bool = False
        self.contacts_skipped: int = 0    # contacts skipped due to cap
        self._since_last_check: int = 0   # new subscribes since last API re-check
        self._alert_sent: bool = False

    @property
    def current_count(self) -> int:
        """Best estimate of current subscribed count."""
        return self.live_count + self.new_subscribes

    @property
    def remaining_slots(self) -> int:
        return max(0, self.cap - self.current_count)

    async def preflight(self) -> bool:
        """
        Pre-flight check: fetch live count and determine if we can proceed.

        Returns:
            True if sync can proceed, False if cap already reached.
        """
        if not self.enabled:
            return True

        stats = await self.mc_client.get_audience_stats()
        self.live_count = stats["member_count"]

        logger.info(f"Audience cap guard: {self.live_count:,} / {self.cap:,} subscribed "
                     f"({self.remaining_slots:,} slots remaining)")

        if self.live_count >= self.cap:
            self.cap_reached = True
            logger.warning(f"AUDIENCE CAP ALREADY REACHED: {self.live_count:,} >= {self.cap:,}")
            await self._send_alert()
            return False

        # Warn if close
        if self.remaining_slots <= 50:
            from corev2.notifications import notify_audience_cap_warning
            await notify_audience_cap_warning(self.live_count, self.cap, self.remaining_slots)

        return True

    async def allow_subscribe(self) -> bool:
        """
        Check whether the next subscribe operation is allowed.

        Call this BEFORE executing an upsert_mc_member operation.
        Returns False if the cap would be exceeded.
        """
        if not self.enabled:
            return True
        if self.cap_reached:
            return False

        # Periodic live re-check
        if self._since_last_check >= self.recheck_interval:
            await self._recheck_live()

        if self.current_count >= self.cap:
            self.cap_reached = True
            logger.warning(f"AUDIENCE CAP HIT during sync: {self.current_count:,} >= {self.cap:,}")
            await self._send_alert()
            return False

        return True

    def record_subscribe(self, action: str):
        """
        Record a successful subscribe (new member or restored from archive).

        Args:
            action: upsert result action — "created" or "restored_from_archive"
        """
        if action in ("created", "restored_from_archive"):
            self.new_subscribes += 1
            self._since_last_check += 1

    async def _recheck_live(self):
        """Re-fetch live count from Mailchimp API."""
        try:
            stats = await self.mc_client.get_audience_stats()
            self.live_count = stats["member_count"]
            # Reset counter — the live count now includes our new subscribes
            self.new_subscribes = 0
            self._since_last_check = 0
            logger.info(f"Audience cap re-check: {self.live_count:,} / {self.cap:,} "
                         f"({self.remaining_slots:,} slots remaining)")
        except Exception as e:
            logger.warning(f"Audience cap re-check failed (using estimate): {e}")

    async def _send_alert(self):
        """Send Teams alert when cap is reached (once per run)."""
        if self._alert_sent:
            return
        self._alert_sent = True
        try:
            from corev2.notifications import notify_audience_cap_reached
            await notify_audience_cap_reached(
                current_count=self.current_count,
                cap=self.cap,
                contacts_synced=self.new_subscribes,
                contacts_skipped=self.contacts_skipped,
            )
        except Exception as e:
            logger.error(f"Failed to send Teams cap alert: {e}")


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
        dry_run: bool = False,
        cap_guard: Optional[AudienceCapGuard] = None,
    ):
        """
        Initialize executor.
        
        Args:
            config: Validated V2Config
            hs_client: HubSpot API client
            mc_client: Mailchimp API client
            dry_run: If True, simulate without mutations
            cap_guard: Optional audience cap guard (shared across executor instances)
        """
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        self.dry_run = dry_run
        self.cap_guard = cap_guard
    
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
                
                # ── Audience cap gate ──────────────────────────────────
                # If the contact's first op is upsert_mc_member (i.e. it
                # may create a new subscriber), check the cap BEFORE we
                # start any ops for this contact.
                has_upsert = any(o.get("type") == "upsert_mc_member" for o in ops)
                if has_upsert and self.cap_guard and self.cap_guard.enabled:
                    if not await self.cap_guard.allow_subscribe():
                        self.cap_guard.contacts_skipped += 1
                        summary["skipped"] += len(ops)
                        summary["total_operations"] += len(ops)
                        journal.log({
                            "event": "contact_skipped_cap",
                            "email": email,
                            "reason": "audience_cap_reached",
                            "cap": self.cap_guard.cap,
                            "current_count": self.cap_guard.current_count,
                        })
                        logger.warning(
                            f"SKIPPED {email}: audience cap {self.cap_guard.cap:,} reached "
                            f"(current {self.cap_guard.current_count:,})"
                        )
                        continue
                # ── End cap gate ───────────────────────────────────────
                
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
        
        # Append audience cap stats
        if self.cap_guard and self.cap_guard.enabled:
            summary["audience_cap"] = {
                "cap": self.cap_guard.cap,
                "current_count": self.cap_guard.current_count,
                "new_subscribes": self.cap_guard.new_subscribes,
                "contacts_skipped": self.cap_guard.contacts_skipped,
                "cap_reached": self.cap_guard.cap_reached,
            }
        
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
            
            # Record new subscribe for audience cap tracking
            if self.cap_guard and result.get("action") in ("created", "restored_from_archive"):
                self.cap_guard.record_subscribe(result["action"])
            
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
