"""
Secondary sync planner - Mailchimp → HubSpot.

Scans Mailchimp for contacts with exit tags and generates operations
to import them back into HubSpot destination (handover) lists.

Flow:
1. Scan entire Mailchimp audience for contacts with configured exit tags
2. Look up each contact in HubSpot by email to get VID
3. Generate operations per contact (in order):
   a) add_hs_to_list: Add to destination handover list in HubSpot
   b) remove_hs_from_list: Remove from source list (MANUAL lists only)
   c) remove_mc_tag: Remove ALL tags from Mailchimp (clean slate before archive)
   d) archive_mc_member: Archive from Mailchimp (journey complete)

Dynamic source lists (987 General) do NOT need manual removal —
HubSpot auto-excludes contacts in handover lists from the dynamic
filter criteria.

Dynamic source list 989 (Sub Agents) also auto-excludes from the
dynamic list, but its static SUBLISTS (900, 972, 971) must be
removed explicitly via additional_remove_lists.

Manual source lists (719, 720, 945, 969) MUST be removed explicitly
because HubSpot cannot auto-manage static list membership.
"""

import asyncio
import logging
from typing import Dict, List, Set, Any, Optional
from datetime import datetime
from corev2.config.schema import V2Config, SecondaryMappingConfig
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mailchimp_client import MailchimpClient

logger = logging.getLogger(__name__)


class SecondaryPlanner:
    """Generates secondary sync operations (Mailchimp → HubSpot)."""

    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MailchimpClient):
        """
        Initialize secondary planner.

        Args:
            config: Validated V2Config with secondary_sync mappings
            hs_client: HubSpot API client
            mc_client: Mailchimp API client
        """
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client

        # Build lookup: exit_tag → mapping config
        self.exit_tag_map: Dict[str, SecondaryMappingConfig] = {}
        for mapping in config.secondary_sync.mappings:
            self.exit_tag_map[mapping.exit_tag] = mapping

        # All exit tags we scan for
        self.exit_tags = set(self.exit_tag_map.keys())

        # Exempt tags: contacts with ANY of these are skipped entirely
        self.exempt_tags = set(config.secondary_sync.exempt_tags)

    async def generate_plan(
        self,
        contact_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Scan Mailchimp for exit-tagged contacts and generate operations.

        Args:
            contact_limit: Max contacts to process (None = unlimited).
                           Overrides config.secondary_sync.contact_limit if set.

        Returns:
            Plan dict with operations and summary
        """
        # Use config limit if no override
        if contact_limit is None and self.config.secondary_sync.contact_limit > 0:
            contact_limit = self.config.secondary_sync.contact_limit

        plan = {
            "plan_type": "secondary_sync",
            "generated_at": datetime.utcnow().isoformat(),
            "config": {
                "exit_tags": sorted(self.exit_tags),
                "contact_limit": contact_limit,
                "archive_after_sync": self.config.secondary_sync.archive_after_sync,
            },
            "summary": {
                "total_mailchimp_scanned": 0,
                "exit_tagged_contacts_found": 0,
                "contacts_by_tag": {},
                "operations_by_type": {},
                "contacts_with_operations": 0,
                "contacts_not_in_hubspot": 0,
            },
            "operations": []
        }

        # Phase 1: Scan Mailchimp for exit-tagged contacts
        logger.info("Phase 1: Scanning Mailchimp for exit-tagged contacts...")
        logger.info(f"  Looking for tags: {sorted(self.exit_tags)}")

        tagged_contacts, total_scanned = await self._scan_mailchimp_for_exit_tags()
        plan["summary"]["total_mailchimp_scanned"] = total_scanned

        total_found = sum(len(contacts) for contacts in tagged_contacts.values())
        plan["summary"]["exit_tagged_contacts_found"] = total_found

        for tag in sorted(tagged_contacts.keys()):
            contacts = tagged_contacts[tag]
            plan["summary"]["contacts_by_tag"][tag] = len(contacts)
            if contacts:
                logger.info(f"  ✓ '{tag}': {len(contacts)} contacts")

        if total_found == 0:
            logger.info("  No exit-tagged contacts found. Nothing to do.")
            return plan

        # Flatten to ordered list for limiting
        all_contacts = []
        for tag in sorted(tagged_contacts.keys()):
            for contact in tagged_contacts[tag]:
                all_contacts.append((tag, contact))

        if contact_limit and len(all_contacts) > contact_limit:
            logger.info(f"  ⚠️  Limiting to {contact_limit} contacts (found {len(all_contacts)})")
            all_contacts = all_contacts[:contact_limit]

        # Phase 2: Look up contacts in HubSpot and generate operations
        logger.info(f"\nPhase 2: Looking up {len(all_contacts)} contacts in HubSpot...")

        contact_groups = []  # List of {email, vid, operations: [...]}
        all_ops_flat = []    # Flat list for summary counting
        contacts_with_ops = 0
        not_in_hubspot = 0

        for exit_tag, contact in all_contacts:
            contact_ops = await self._generate_operations_for_contact(
                contact, exit_tag
            )

            if contact_ops is None:
                # Contact not found in HubSpot
                not_in_hubspot += 1
            elif contact_ops:
                # Group operations under a contact entry (executor expects this format)
                contact_groups.append({
                    "email": contact["email"],
                    "vid": contact_ops[0].get("vid"),  # VID from first op
                    "operations": contact_ops
                })
                all_ops_flat.extend(contact_ops)
                contacts_with_ops += 1

        plan["operations"] = contact_groups
        plan["summary"]["contacts_with_operations"] = contacts_with_ops
        plan["summary"]["contacts_not_in_hubspot"] = not_in_hubspot

        # Count operations by type
        for op in all_ops_flat:
            op_type = op["type"]
            plan["summary"]["operations_by_type"][op_type] = \
                plan["summary"]["operations_by_type"].get(op_type, 0) + 1

        logger.info(f"\n✓ Secondary sync plan generated:")
        logger.info(f"  Mailchimp members scanned: {total_scanned}")
        logger.info(f"  Exit-tagged contacts found: {total_found}")
        logger.info(f"  Contacts with operations: {contacts_with_ops}")
        if not_in_hubspot:
            logger.warning(f"  ⚠️  Not found in HubSpot: {not_in_hubspot}")
        for op_type, count in sorted(plan["summary"]["operations_by_type"].items()):
            logger.info(f"    • {op_type}: {count}")

        return plan

    async def _scan_mailchimp_for_exit_tags(self) -> tuple:
        """
        Scan Mailchimp audience for contacts with exit tags.

        Returns:
            Tuple of (tagged_contacts dict, total_members_scanned)
            tagged_contacts: Dict mapping exit_tag → list of contact dicts
        """
        tagged_contacts: Dict[str, List[Dict]] = {tag: [] for tag in self.exit_tags}

        member_count = 0

        async for member in self.mc_client.get_all_members(count=1000):
            member_count += 1

            if member_count % 500 == 0:
                logger.info(f"  Scanned {member_count} Mailchimp members...")

            member_tags = set(member.get("tags", []))

            # Check if member has any exit tags
            matching_tags = member_tags & self.exit_tags

            if matching_tags:
                # Only process subscribed/unsubscribed members (not cleaned/archived)
                status = member.get("status", "")
                if status in ("cleaned", "archived"):
                    logger.debug(
                        f"  Skipping {member['email_address']} with exit tag "
                        f"(status={status})"
                    )
                    continue

                # Skip contacts with exempt tags (e.g. Manual Inclusion)
                if self.exempt_tags & member_tags:
                    logger.info(
                        f"  Skipping {member['email_address']}: has exempt tag "
                        f"{self.exempt_tags & member_tags} — leaving in Mailchimp"
                    )
                    continue

                for tag in matching_tags:
                    tagged_contacts[tag].append({
                        "email": member["email_address"],
                        "status": status,
                        "tags": list(member_tags),
                        "merge_fields": member.get("merge_fields", {}),
                    })

        logger.info(f"  Scan complete: {member_count} members scanned")
        return tagged_contacts, member_count

    async def _generate_operations_for_contact(
        self,
        contact: Dict[str, Any],
        exit_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Generate operations for a single exit-tagged contact.

        Args:
            contact: Mailchimp contact data
            exit_tag: The exit tag found on this contact

        Returns:
            List of operation dicts, or None if contact not found in HubSpot
        """
        email = contact["email"]
        mapping = self.exit_tag_map[exit_tag]
        operations = []

        # Look up contact in HubSpot
        hs_contact = await self.hs_client.get_contact_by_email(email)

        if not hs_contact["found"]:
            logger.warning(
                f"  ⚠️  {email}: has exit tag '{exit_tag}' but NOT found in HubSpot — skipping"
            )
            return None

        vid = hs_contact["vid"]

        logger.info(
            f"  {email}: exit_tag='{exit_tag}' → "
            f"{'add to ' + mapping.destination_name + ' (' + mapping.destination_list + ')' if mapping.destination_list else 'MC cleanup only (no HubSpot handover)'}, "
            f"source={mapping.source_name} ({mapping.source_list})"
            f"{', REMOVE from source' if mapping.remove_from_source else ''}"
            f"{f', REMOVE from {len(mapping.additional_remove_lists)} sublists' if mapping.additional_remove_lists else ''} "
            f"(VID: {vid})"
        )

        # Operation 1: Add to destination handover list (if configured)
        if mapping.destination_list:
            operations.append({
                "type": "add_hs_to_list",
                "email": email,
                "vid": vid,
                "list_id": mapping.destination_list,
                "list_name": mapping.destination_name,
                "reason": f"secondary_sync:{exit_tag}"
            })

        # Operation 2: Remove from source list (only for manual/static lists)
        if mapping.remove_from_source:
            operations.append({
                "type": "remove_hs_from_list",
                "email": email,
                "vid": vid,
                "list_id": mapping.source_list,
                "list_name": mapping.source_name,
                "reason": f"anti_remarketing:{exit_tag}"
            })

        # Operation 2b: Remove from additional static source lists
        # (e.g. Sub Agents 989 is dynamic, fed by static sublists 900/972/971)
        for extra_list in mapping.additional_remove_lists:
            operations.append({
                "type": "remove_hs_from_list",
                "email": email,
                "vid": vid,
                "list_id": extra_list.list_id,
                "list_name": extra_list.list_name,
                "reason": f"anti_remarketing_sublist:{exit_tag}"
            })

        # Operation 3: Remove ALL tags from Mailchimp (clean slate before archive)
        # This removes the exit tag plus any other tags the contact has
        if self.config.secondary_sync.archive_after_sync:
            all_tags = contact.get("tags", [])
            if all_tags:
                operations.append({
                    "type": "remove_mc_tag",
                    "email": email,
                    "tags": all_tags,
                    "reason": f"pre_archive_cleanup:{exit_tag}"
                })

        # Operation 4: Archive from Mailchimp (if enabled)
        if self.config.secondary_sync.archive_after_sync:
            operations.append({
                "type": "archive_mc_member",
                "email": email,
                "reason": f"secondary_sync_complete:{exit_tag}"
            })

        return operations
