"""
Sync Executor - applies an operations plan against live APIs.

Handles all 8 operation types:
  upsert_mc_member      Create/update contact in Mautic
  apply_mc_tag          Add tag to Mautic contact
  remove_mc_tag         Remove tag(s) from Mautic contact
  unsubscribe_mc_member Mark contact as Do Not Contact in Mautic
  archive_mc_member     Soft-delete contact from Mautic
  update_hs_property    Write ORI_LISTS property back to HubSpot
  add_hs_to_list        Add contact to a HubSpot static list
  remove_hs_from_list   Remove contact from a HubSpot static list

AudienceCapGuard:
  - Pre-flight: fetches live Mautic count, aborts if >= cap
  - Per-contact: skips new upserts if cap reached
  - Re-checks live count every 10 new subscribers
  - Sends Teams alert on cap reached / proximity warning

All operations are logged to a JSONL journal for auditability.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from corev2.config.schema import V2Config
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mautic_client import MauticClient

logger = logging.getLogger(__name__)

_CAP_RECHECK_INTERVAL = 10  # Re-fetch live count every N new subscribes
_CAP_WARNING_THRESHOLD = 50  # Send Teams alert if < N slots remain


class AudienceCapGuard:
    """
    Enforces Mautic audience cap (max subscribed contacts).

    Pre-flight check fetches live count and aborts if >= cap.
    Per-contact check tracks new subscribers and re-fetches periodically.
    """

    def __init__(
        self,
        mc_client: MauticClient,
        cap: int,
        webhook_url: Optional[str] = None,
    ):
        self.mc_client = mc_client
        self.cap = cap
        self.webhook_url = webhook_url
        self.current_count: int = 0
        self.new_subscribes: int = 0
        self.cap_reached: bool = False
        self.skipped: int = 0

    async def preflight_check(self) -> bool:
        """
        Fetch live Mautic count.
        Returns True if sync can proceed, False if cap already reached.
        """
        self.current_count = await self.mc_client.get_subscribed_count()
        remaining = self.cap - self.current_count
        logger.info(f"Audience cap: {self.current_count}/{self.cap} ({remaining} slots remaining)")

        if self.current_count >= self.cap:
            logger.error(f"AUDIENCE CAP REACHED ({self.current_count}/{self.cap}) - aborting sync")
            self.cap_reached = True
            if self.webhook_url:
                from corev2.notifications import send_audience_cap_reached
                await send_audience_cap_reached(self.webhook_url, self.current_count, self.cap, 0)
            return False

        if remaining < _CAP_WARNING_THRESHOLD:
            logger.warning(f"AUDIENCE CAP WARNING: only {remaining} slots remaining")
            if self.webhook_url:
                from corev2.notifications import send_audience_cap_warning
                await send_audience_cap_warning(self.webhook_url, self.current_count, self.cap, remaining)

        return True

    async def can_add_subscriber(self) -> bool:
        """Check if a new subscriber can be added."""
        if self.cap_reached:
            return False
        # Periodic live re-check
        if self.new_subscribes > 0 and self.new_subscribes % _CAP_RECHECK_INTERVAL == 0:
            self.current_count = await self.mc_client.get_subscribed_count()
            logger.debug(f"Cap re-check: {self.current_count}/{self.cap}")

        if self.current_count >= self.cap:
            self.cap_reached = True
            logger.warning(f"Audience cap hit mid-run ({self.current_count}/{self.cap})")
            if self.webhook_url:
                from corev2.notifications import send_audience_cap_reached
                await send_audience_cap_reached(self.webhook_url, self.current_count, self.cap, self.skipped)
            return False
        return True

    def record_new_subscriber(self):
        self.new_subscribes += 1
        self.current_count += 1

    def record_skipped(self):
        self.skipped += 1


class OperationJournal:
    """JSONL append-only execution journal for auditability."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "a", encoding="utf-8")

    def log(self, entry: Dict[str, Any]):
        entry["ts"] = datetime.utcnow().isoformat()
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SyncExecutor:
    """Executes operations from a plan dict."""

    def __init__(
        self,
        config: V2Config,
        hs_client: HubSpotClient,
        mc_client: MauticClient,
        dry_run: bool = False,
    ):
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        self.dry_run = dry_run
        self.cap_guard: Optional[AudienceCapGuard] = None

    async def execute_plan(
        self,
        plan: Dict[str, Any],
        journal_path: Optional[Path] = None,
        cap_guard: Optional[AudienceCapGuard] = None,
    ) -> Dict[str, Any]:
        """Execute all operations in the plan."""
        if journal_path is None:
            journal_path = Path("corev2/artifacts/execution_journal.jsonl")

        self.cap_guard = cap_guard

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
            journal.log({"event": "execution_started", "dry_run": self.dry_run})

            for contact_ops in plan.get("operations", []):
                email = contact_ops.get("email", "unknown")
                ops = contact_ops.get("operations", [])
                summary["contacts_processed"] += 1
                logger.info(f"Processing: {email}")

                for op in ops:
                    summary["total_operations"] += 1
                    try:
                        result = await self._execute_op(op, journal)
                        if result["success"]:
                            summary["successful"] += 1
                        elif result.get("skipped"):
                            summary["skipped"] += 1
                        else:
                            summary["failed"] += 1
                    except Exception as e:
                        logger.error(f"  {op.get('type')} failed for {email}: {e}")
                        journal.log({"event": "op_error", "email": email, "op": op.get("type"), "error": str(e)})
                        summary["failed"] += 1

            journal.log({"event": "execution_complete", "summary": summary})

        summary["ended_at"] = datetime.utcnow().isoformat()
        if self.cap_guard and self.cap_guard.skipped:
            summary["cap_skipped"] = self.cap_guard.skipped
        logger.info(
            f"Execution complete: {summary['successful']} ok, "
            f"{summary['failed']} failed, {summary['skipped']} skipped"
        )
        return summary

    async def _execute_op(self, op: Dict[str, Any], journal: OperationJournal) -> Dict[str, Any]:
        t = op.get("type")
        if t == "upsert_mc_member":
            return await self._upsert(op, journal)
        elif t == "apply_mc_tag":
            return await self._apply_tag(op, journal)
        elif t == "remove_mc_tag":
            return await self._remove_tag(op, journal)
        elif t == "unsubscribe_mc_member":
            return await self._unsubscribe(op, journal)
        elif t == "archive_mc_member":
            return await self._archive(op, journal)
        elif t == "update_hs_property":
            return await self._update_hs_property(op, journal)
        elif t == "add_hs_to_list":
            return await self._add_hs_to_list(op, journal)
        elif t == "remove_hs_from_list":
            return await self._remove_hs_from_list(op, journal)
        else:
            logger.warning(f"Unknown operation type: {t}")
            journal.log({"event": "op_skipped", "op": t, "reason": "unknown_type"})
            return {"success": False, "skipped": True}

    # ------------------------------------------------------------------
    # Individual operation handlers
    # ------------------------------------------------------------------

    async def _upsert(self, op, journal):
        email = op["email"]
        merge_fields = op.get("merge_fields", {})
        status_if_new = op.get("status_if_new", "subscribed")

        if self.dry_run:
            journal.log({"event": "simulated", "op": "upsert_mc_member", "email": email})
            return {"success": True, "skipped": False}

        # Audience cap check
        if self.cap_guard:
            if not await self.cap_guard.can_add_subscriber():
                self.cap_guard.record_skipped()
                logger.warning(f"  Skipping {email}: audience cap reached")
                journal.log({"event": "op_skipped", "op": "upsert_mc_member", "email": email, "reason": "audience_cap"})
                return {"success": False, "skipped": True}

        try:
            result = await self.mc_client.upsert_member(email, merge_fields, status_if_new)
            if result.get("action") in ("created", "restored_from_archive") and self.cap_guard:
                self.cap_guard.record_new_subscriber()
            # Clean stale tags after restore
            if result.get("action") == "restored_from_archive":
                member = await self.mc_client.get_member(email)
                stale = member.get("tags", [])
                if stale:
                    await self.mc_client.remove_tags(email, stale)
                    logger.info(f"  Cleaned {len(stale)} stale tags after restore: {email}")
            journal.log({"event": "op_executed", "op": "upsert_mc_member", "email": email, "action": result.get("action")})
            return {"success": True, "skipped": False}
        except Exception as e:
            if "compliance" in str(e).lower() or "doNotContact" in str(e):
                logger.warning(f"  Compliance state - skipping {email}")
                journal.log({"event": "op_skipped", "op": "upsert_mc_member", "email": email, "reason": "compliance"})
                return {"success": False, "skipped": True}
            raise

    async def _apply_tag(self, op, journal):
        email = op["email"]
        tag = op["tag"]
        if self.dry_run:
            journal.log({"event": "simulated", "op": "apply_mc_tag", "email": email, "tag": tag})
            return {"success": True, "skipped": False}
        await self.mc_client.add_tags(email, [tag])
        journal.log({"event": "op_executed", "op": "apply_mc_tag", "email": email, "tag": tag})
        return {"success": True, "skipped": False}

    async def _remove_tag(self, op, journal):
        email = op["email"]
        tags = op.get("tags") or ([op["tag"]] if op.get("tag") else [])
        if not tags:
            return {"success": True, "skipped": True}
        if self.dry_run:
            journal.log({"event": "simulated", "op": "remove_mc_tag", "email": email, "tags": tags})
            return {"success": True, "skipped": False}
        await self.mc_client.remove_tags(email, tags)
        journal.log({"event": "op_executed", "op": "remove_mc_tag", "email": email, "tags": tags})
        return {"success": True, "skipped": False}

    async def _unsubscribe(self, op, journal):
        email = op["email"]
        if self.dry_run:
            journal.log({"event": "simulated", "op": "unsubscribe_mc_member", "email": email})
            return {"success": True, "skipped": False}
        try:
            result = await self.mc_client.unsubscribe_member(email)
            journal.log({"event": "op_executed", "op": "unsubscribe_mc_member", "email": email, "action": result.get("action")})
            return {"success": True, "skipped": result.get("action") == "already_unsubscribed"}
        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": True, "skipped": True}
            raise

    async def _archive(self, op, journal):
        email = op["email"]
        if self.dry_run:
            journal.log({"event": "simulated", "op": "archive_mc_member", "email": email})
            return {"success": True, "skipped": False}
        if not self.config.safety.allow_archive:
            logger.warning(f"  Archival disabled - skipping {email}")
            journal.log({"event": "op_skipped", "op": "archive_mc_member", "email": email, "reason": "archival_disabled"})
            return {"success": False, "skipped": True}
        result = await self.mc_client.archive_member(email)
        journal.log({"event": "op_executed", "op": "archive_mc_member", "email": email, "action": result.get("action")})
        logger.info(f"  Archived {email}")
        return {"success": True, "skipped": False}

    async def _update_hs_property(self, op, journal):
        vid = op["vid"]
        prop = op["property"]
        value = op["value"]
        if self.dry_run:
            journal.log({"event": "simulated", "op": "update_hs_property", "vid": vid, "property": prop})
            return {"success": True, "skipped": False}
        await self.hs_client.update_contact_property(vid, prop, value)
        journal.log({"event": "op_executed", "op": "update_hs_property", "vid": vid, "property": prop})
        return {"success": True, "skipped": False}

    async def _add_hs_to_list(self, op, journal):
        list_id = op["list_id"]
        vid = op["vid"]
        email = op.get("email", "unknown")
        if self.dry_run:
            journal.log({"event": "simulated", "op": "add_hs_to_list", "list_id": list_id, "vid": vid})
            return {"success": True, "skipped": False}
        try:
            await self.hs_client.add_contact_to_list(list_id, vid)
            journal.log({"event": "op_executed", "op": "add_hs_to_list", "list_id": list_id, "vid": vid, "email": email})
            logger.info(f"  Added {email} to HubSpot list {list_id} ({op.get('list_name', '')})")
            return {"success": True, "skipped": False}
        except Exception as e:
            if "already" in str(e).lower():
                return {"success": True, "skipped": True}
            raise

    async def _remove_hs_from_list(self, op, journal):
        list_id = op["list_id"]
        vid = op["vid"]
        if self.dry_run:
            journal.log({"event": "simulated", "op": "remove_hs_from_list", "list_id": list_id, "vid": vid})
            return {"success": True, "skipped": False}
        try:
            await self.hs_client.remove_contact_from_list(list_id, vid)
            journal.log({"event": "op_executed", "op": "remove_hs_from_list", "list_id": list_id, "vid": vid})
            return {"success": True, "skipped": False}
        except Exception as e:
            if "404" in str(e):
                return {"success": True, "skipped": True}
            raise
