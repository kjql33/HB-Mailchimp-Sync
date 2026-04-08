"""
Primary Sync Planner - HubSpot → Mautic.

Generates a deterministic operations plan by:
1. Scanning all configured HubSpot lists (batch API, ~100x faster than one-by-one)
2. Applying the 4-group exclusion matrix
3. Resolving the correct tag per contact (branch split via tag_overrides)
4. Checking existing Mautic tags (skipped on fresh install for performance)
5. Running archival reconciliation if allow_archive=true

Invariants enforced:
- INV-002: Compliance lists 762, 773 → never synced
- INV-004: First-tag priority - existing valid tags are preserved
- INV-005: Never resubscribe opted-out contacts (enforced in executor)
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Set, Any, Optional, Tuple

from corev2.config.schema import V2Config, ExclusionMatrixGroupConfig, ListConfig
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mautic_client import MauticClient

logger = logging.getLogger(__name__)

_COMPLIANCE_LISTS = {"762", "773"}
_GROUP_ORDER = ["general_marketing", "special_campaigns", "manual_override", "long_term_marketing"]


class SyncPlanner:
    """Generates primary sync operations plan (read-only, no mutations)."""

    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MauticClient):
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        self._mautic_has_contacts: bool = True  # set during generate_plan
        # Build list_id → ListConfig lookup
        self._list_config_map: Dict[str, ListConfig] = {}
        for group_lists in config.hubspot.lists.values():
            for lc in group_lists:
                self._list_config_map[lc.id] = lc

    # ------------------------------------------------------------------
    # Tag resolution
    # ------------------------------------------------------------------

    def _evaluate_condition(self, condition: str, props: Dict[str, Any]) -> bool:
        """
        Evaluate a simple condition string against contact properties.
        Supports: >, <, >=, <=, ==
        Example: "branches > 1"
        """
        try:
            for op in (">=", "<=", ">", "<", "=="):
                if op in condition:
                    left, right = condition.split(op, 1)
                    prop_name = left.strip()
                    threshold = right.strip()
                    raw = props.get(prop_name)
                    if raw is None:
                        return False
                    try:
                        lval = float(str(raw))
                        rval = float(threshold)
                        return {">": lval > rval, "<": lval < rval,
                                ">=": lval >= rval, "<=": lval <= rval,
                                "==": lval == rval}[op]
                    except (ValueError, TypeError):
                        return str(raw) == threshold
        except Exception as e:
            logger.debug(f"Condition eval error '{condition}': {e}")
        return False

    def _resolve_tags(self, list_config: ListConfig, props: Dict[str, Any]) -> List[str]:
        """
        Resolve tags for a contact on a given list.
        Applies tag_overrides first (branch split), then additional_tags.
        """
        primary = list_config.tag
        for override in list_config.tag_overrides:
            if self._evaluate_condition(override.condition, props):
                primary = override.tag
                break
        return [primary] + list_config.additional_tags

    # ------------------------------------------------------------------
    # Exclusion matrix
    # ------------------------------------------------------------------

    def _determine_target_tags(
        self,
        contact_list_ids: Set[str],
        contact_props: Dict[str, Any],
        email: str,
    ) -> Optional[Tuple[List[str], str]]:
        """
        Walk groups in priority order. Return (tags, list_id) for first eligible match.
        Returns None if contact is excluded from all groups.
        """
        for group_name in _GROUP_ORDER:
            group_cfg: ExclusionMatrixGroupConfig = getattr(self.config.exclusion_matrix, group_name)
            is_excluded = bool(contact_list_ids & set(group_cfg.exclude))

            for list_id in group_cfg.lists:
                if list_id not in contact_list_ids:
                    continue
                if is_excluded:
                    logger.debug(f"{email}: in list {list_id} but excluded from {group_name}")
                    continue
                lc = self._list_config_map.get(list_id)
                if not lc:
                    logger.warning(f"No ListConfig for list_id={list_id}")
                    continue
                tags = self._resolve_tags(lc, contact_props)
                logger.debug(f"{email}: matched list {list_id} → tags {tags}")
                return tags, list_id

        return None

    # ------------------------------------------------------------------
    # Plan generation
    # ------------------------------------------------------------------

    async def generate_plan(
        self,
        contact_limit: Optional[int] = None,
        only_email: Optional[str] = None,
        only_vid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate operations plan (read-only).

        Returns a plan dict with:
            metadata, summary, operations[]
        """
        logger.info("Starting plan generation...")

        plan: Dict[str, Any] = {
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "config_hash": "",
                "config_file": "",
                "contact_limit": contact_limit or 0,
                "run_mode": self.config.safety.run_mode.value,
            },
            "summary": {
                "total_contacts_scanned": 0,
                "contacts_with_operations": 0,
                "operations_by_type": {},
            },
            "operations": [],
        }

        # Build sets of list IDs to scan
        all_sync_ids: Set[str] = set()
        for group_name in _GROUP_ORDER:
            all_sync_ids.update(getattr(self.config.exclusion_matrix, group_name).lists)

        exclusion_ids: Set[str] = set()
        exclusion_ids.update(self.config.hubspot.exclusions.critical)
        exclusion_ids.update(self.config.hubspot.exclusions.active_deals)
        exclusion_ids.update(self.config.hubspot.exclusions.exit)

        all_lists_to_scan = all_sync_ids | exclusion_ids

        logger.info(f"Scanning {len(all_sync_ids)} sync lists + {len(exclusion_ids)} exclusion lists")

        # Aggregate contacts from all lists
        contacts_by_email: Dict[str, Dict] = {}

        for list_id in sorted(all_lists_to_scan):
            logger.info(f"Fetching members of list {list_id}...")
            count = 0
            try:
                async for contact in self.hs_client.get_list_members(
                    list_id,
                    properties=["email", "firstname", "lastname", "branches",
                                self.config.sync.ori_lists_field],
                ):
                    email = contact.get("email")
                    if not email:
                        continue
                    if email not in contacts_by_email:
                        contacts_by_email[email] = {
                            "vid": contact["vid"],
                            "email": email,
                            "properties": contact["properties"],
                            "list_ids": set(),
                        }
                    contacts_by_email[email]["list_ids"].add(list_id)
                    count += 1
                    if contact_limit and len(contacts_by_email) >= contact_limit:
                        break
            except Exception as e:
                logger.error(f"Error fetching list {list_id}: {e}")
                raise
            logger.info(f"  {count} contacts in list {list_id}")
            if contact_limit and len(contacts_by_email) >= contact_limit:
                break

        total = len(contacts_by_email)
        logger.info(f"Total unique contacts: {total}")
        plan["summary"]["total_contacts_scanned"] = total

        # Check if Mautic has any contacts (fresh-install optimisation)
        # On fresh install, skip per-contact Mautic lookup (saves ~4000 API calls)
        try:
            mautic_count = await self.mc_client.get_subscribed_count()
            self._mautic_has_contacts = mautic_count > 0
            if not self._mautic_has_contacts:
                logger.info("Fresh Mautic install - skipping per-contact tag checks (INV-004a)")
            else:
                logger.info(f"Mautic has {mautic_count} contacts - checking existing tags")
        except Exception as e:
            logger.warning(f"Could not check Mautic count: {e} - defaulting to checking tags")
            self._mautic_has_contacts = True

        # Apply single-contact filter
        if only_email or only_vid:
            filtered: Dict[str, Dict] = {}
            if only_email and only_email in contacts_by_email:
                filtered[only_email] = contacts_by_email[only_email]
            elif only_vid:
                for em, data in contacts_by_email.items():
                    if str(data["vid"]) == str(only_vid):
                        filtered[em] = data
                        break
            contacts_by_email = filtered
            logger.info(f"Filtered to {len(contacts_by_email)} contact(s)")

        all_source_tags = self.config.get_all_source_tags()

        # Generate per-contact operations
        for email, data in contacts_by_email.items():
            ops = await self._plan_contact_ops(
                email, data["vid"], data["list_ids"],
                data["properties"], all_source_tags,
            )
            if ops:
                plan["operations"].append({"email": email, "vid": data["vid"], "operations": ops})
                plan["summary"]["contacts_with_operations"] += 1
                for op in ops:
                    t = op["type"]
                    plan["summary"]["operations_by_type"][t] = (
                        plan["summary"]["operations_by_type"].get(t, 0) + 1
                    )

        logger.info(f"Plan complete: {plan['summary']['contacts_with_operations']} contacts with operations")
        return plan

    async def _plan_contact_ops(
        self,
        email: str,
        vid: Any,
        list_ids: Set[str],
        props: Dict[str, Any],
        all_source_tags: Set[str],
    ) -> List[Dict[str, Any]]:
        """Plan operations for a single contact."""
        # INV-002: Skip compliance list members
        if _COMPLIANCE_LISTS & list_ids:
            logger.debug(f"{email}: in compliance list - skipping")
            return []

        # Determine target tags via exclusion matrix
        result = self._determine_target_tags(list_ids, props, email)
        if result is None:
            logger.debug(f"{email}: excluded from all groups")
            return []

        target_tags, _ = result

        # INV-004: First-tag priority - preserve existing valid Mautic tags
        existing_tags: List[str] = []
        if self._mautic_has_contacts:
            try:
                mc_member = await self.mc_client.get_member(email)
                if mc_member["found"]:
                    existing_tags = mc_member.get("tags", [])
                    current_source = [t for t in existing_tags if t in all_source_tags]
                    if current_source:
                        logger.info(f"{email}: preserving existing tag '{current_source[0]}' (INV-004)")
                        target_tags = [current_source[0]] + [
                            t for t in target_tags if t not in current_source
                        ]
            except Exception as e:
                if "404" not in str(e) and "not found" not in str(e).lower():
                    logger.error(f"STRICT: failed to fetch Mautic state for {email}: {e} - skipping")
                    return []

        operations: List[Dict[str, Any]] = []

        # Helper to get property safely
        def get_prop(name: str) -> str:
            v = props.get(name, "")
            return str(v.get("value", "") if isinstance(v, dict) else v or "")

        # Operation 1: Upsert contact in Mautic
        operations.append({
            "type": "upsert_mc_member",
            "email": email,
            "merge_fields": {"FNAME": get_prop("firstname"), "LNAME": get_prop("lastname")},
            "status_if_new": "subscribed",
        })

        # Operation 2: Remove stale source tags
        stale = [t for t in existing_tags if t in all_source_tags and t not in target_tags]
        if stale:
            operations.append({"type": "remove_mc_tag", "email": email, "tags": stale})

        # Operation 3: Apply target tags
        for tag in target_tags:
            operations.append({"type": "apply_mc_tag", "email": email, "tag": tag})

        # Operation 4: ORI_LISTS write-back to HubSpot
        if self.config.safety.enable_hubspot_writes:
            operations.append({
                "type": "update_hs_property",
                "vid": vid,
                "property": self.config.sync.ori_lists_field,
                "value": ",".join(sorted(list_ids)),
            })

        return operations
