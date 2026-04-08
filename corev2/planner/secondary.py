"""
Secondary sync planner - Mautic -> HubSpot.

Updated rules (2026-04-02):
- 9 exit tag mappings (was 6)
- Optional destination_list (Long Term = MC cleanup only, no HubSpot handover)
- additional_remove_lists for Sub Agents (removes from 900, 972, 971)
- exempt_tags: contacts with "Manual Inclusion" tag skipped entirely (SEC-008)
- All safety rules SEC-001 through SEC-010 implemented
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from corev2.config.schema import V2Config, SecondaryMappingConfig
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mautic_client import MauticClient

logger = logging.getLogger(__name__)


class SecondaryPlanner:
    """Generates secondary sync operations (Mautic -> HubSpot)."""

    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MauticClient):
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        self.exit_tag_map: Dict[str, SecondaryMappingConfig] = {
            m.exit_tag: m for m in config.secondary_sync.mappings
        }
        self.exit_tags = set(self.exit_tag_map.keys())
        self.exempt_tags = set(config.secondary_sync.exempt_tags)

    async def generate_plan(self, contact_limit: Optional[int] = None) -> Dict[str, Any]:
        if contact_limit is None and self.config.secondary_sync.contact_limit > 0:
            contact_limit = self.config.secondary_sync.contact_limit

        plan = {
            "plan_type": "secondary_sync",
            "generated_at": datetime.utcnow().isoformat(),
            "config": {
                "exit_tags": sorted(self.exit_tags),
                "exempt_tags": sorted(self.exempt_tags),
                "contact_limit": contact_limit,
                "archive_after_sync": self.config.secondary_sync.archive_after_sync,
            },
            "summary": {
                "total_mailchimp_scanned": 0,
                "exit_tagged_contacts_found": 0,
                "exempt_contacts_skipped": 0,
                "contacts_by_tag": {},
                "operations_by_type": {},
                "contacts_with_operations": 0,
                "contacts_not_in_hubspot": 0,
            },
            "operations": [],
        }

        logger.info("Phase 1: Scanning Mautic for exit-tagged contacts...")
        tagged_contacts, total_scanned, exempt_skipped = await self._scan_for_exit_tags()
        plan["summary"]["total_mailchimp_scanned"] = total_scanned
        plan["summary"]["exempt_contacts_skipped"] = exempt_skipped

        total_found = sum(len(v) for v in tagged_contacts.values())
        plan["summary"]["exit_tagged_contacts_found"] = total_found

        for tag in sorted(tagged_contacts.keys()):
            plan["summary"]["contacts_by_tag"][tag] = len(tagged_contacts[tag])
            if tagged_contacts[tag]:
                logger.info(f"  '{tag}': {len(tagged_contacts[tag])} contacts")

        if total_found == 0:
            logger.info("No exit-tagged contacts found.")
            return plan

        all_contacts = []
        for tag in sorted(tagged_contacts.keys()):
            for contact in tagged_contacts[tag]:
                all_contacts.append((tag, contact))

        if contact_limit and len(all_contacts) > contact_limit:
            logger.info(f"Limiting to {contact_limit} contacts (found {len(all_contacts)})")
            all_contacts = all_contacts[:contact_limit]

        logger.info(f"\nPhase 2: Processing {len(all_contacts)} exit-tagged contacts...")
        contact_groups = []
        all_ops_flat = []
        contacts_with_ops = 0
        not_in_hubspot = 0

        for exit_tag, contact in all_contacts:
            ops = await self._generate_operations(contact, exit_tag)
            if ops is None:
                not_in_hubspot += 1
            elif ops:
                contact_groups.append({
                    "email": contact["email"],
                    "vid": ops[0].get("vid"),
                    "operations": ops,
                })
                all_ops_flat.extend(ops)
                contacts_with_ops += 1

        plan["operations"] = contact_groups
        plan["summary"]["contacts_with_operations"] = contacts_with_ops
        plan["summary"]["contacts_not_in_hubspot"] = not_in_hubspot

        for op in all_ops_flat:
            t = op["type"]
            plan["summary"]["operations_by_type"][t] = plan["summary"]["operations_by_type"].get(t, 0) + 1

        logger.info(f"Secondary plan: {contacts_with_ops} contacts with operations, {not_in_hubspot} not found in HubSpot")
        return plan

    async def _scan_for_exit_tags(self) -> Tuple[Dict[str, List], int, int]:
        """Scan Mautic for contacts with exit tags. Returns (tagged, total_scanned, exempt_skipped)."""
        tagged: Dict[str, List] = {tag: [] for tag in self.exit_tags}
        count = 0
        exempt_skipped = 0

        async for member in self.mc_client.get_all_members(count=200):
            count += 1
            if count % 500 == 0:
                logger.info(f"  Scanned {count} Mautic contacts...")

            member_tags = set(member.get("tags", []))
            matching_exit_tags = member_tags & self.exit_tags
            if not matching_exit_tags:
                continue

            # SEC-008: Skip contacts with exempt tags entirely
            if member_tags & self.exempt_tags:
                exempt_skipped += 1
                logger.debug(f"  {member['email_address']}: has exempt tag - skipping secondary sync")
                continue

            status = member.get("status", "")
            if status in ("cleaned", "archived"):
                logger.debug(f"  {member['email_address']}: status={status} - skipping")
                continue

            for tag in matching_exit_tags:
                tagged[tag].append({
                    "email": member["email_address"],
                    "status": status,
                    "tags": list(member_tags),
                })

        logger.info(f"Scan complete: {count} contacts scanned, {exempt_skipped} exempt skipped")
        return tagged, count, exempt_skipped

    async def _generate_operations(
        self, contact: Dict[str, Any], exit_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Generate operations for a single exit-tagged contact.
        Returns None if contact not found in HubSpot (SEC-003).
        """
        email = contact["email"]
        mapping = self.exit_tag_map[exit_tag]
        operations = []

        hs_contact = await self.hs_client.get_contact_by_email(email)
        if not hs_contact["found"]:
            logger.warning(f"  {email}: exit tag '{exit_tag}' but NOT found in HubSpot - skipping (SEC-003)")
            return None

        vid = hs_contact["vid"]
        logger.info(
            f"  {email}: exit_tag='{exit_tag}' -> "
            f"{'add to ' + mapping.destination_name if mapping.destination_list else 'MC cleanup only'}"
            f", source={mapping.source_name} (VID: {vid})"
        )

        # Step 1: Add to destination handover list (only if destination_list is set)
        if mapping.destination_list:
            operations.append({
                "type": "add_hs_to_list",
                "email": email,
                "vid": vid,
                "list_id": mapping.destination_list,
                "list_name": mapping.destination_name,
                "reason": f"secondary_sync:{exit_tag}",
            })

        # Step 2: Remove from source list (manual lists only)
        if mapping.remove_from_source:
            operations.append({
                "type": "remove_hs_from_list",
                "email": email,
                "vid": vid,
                "list_id": mapping.source_list,
                "list_name": mapping.source_name,
                "reason": f"anti_remarketing:{exit_tag}",
            })

        # Step 2b: Remove from additional lists (Sub Agents: 900, 972, 971)
        for extra_list in mapping.additional_remove_lists:
            operations.append({
                "type": "remove_hs_from_list",
                "email": email,
                "vid": vid,
                "list_id": extra_list.list_id,
                "list_name": extra_list.list_name,
                "reason": f"additional_cleanup:{exit_tag}",
            })

        # Step 3: Remove ALL Mautic tags (clean slate before archive - SEC-004)
        if self.config.secondary_sync.archive_after_sync:
            all_tags = contact.get("tags", [])
            if all_tags:
                operations.append({
                    "type": "remove_mc_tag",
                    "email": email,
                    "tags": all_tags,
                    "reason": f"pre_archive_cleanup:{exit_tag}",
                })

        # Step 4: Archive from Mautic
        if self.config.secondary_sync.archive_after_sync:
            operations.append({
                "type": "archive_mc_member",
                "email": email,
                "reason": f"secondary_sync_complete:{exit_tag}",
            })

        return operations
