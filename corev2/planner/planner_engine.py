"""
Primary Sync Planner - Generates deterministic operations_plan.json

Enforces V1 Behavioral Invariants:
- INV-001: Three-tier import stream architecture (exact exclusion matrix)
- INV-002: Compliance lists (762, 773) NEVER synced - DYNAMIC LISTS auto-managed by HubSpot
- INV-003: Single-tag enforcement (first-wins)
- INV-004: ORI_LISTS source tracking (single-value string)
- INV-007: Force subscribe behavior (all contacts → "subscribed")

Generates operations for:
- upsert_mc_member: Create/update Mailchimp member (never resubscribe)
- apply_mc_tag: Apply HubSpot list tag (single-tag rule)
- remove_mc_tag: Remove orphaned tags
- archive_mc_member: Archive fully-orphaned contacts
- update_hs_property: Update HubSpot properties (ORI_LISTS)
"""

import hashlib
import json
from typing import Dict, List, Set, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, asdict


@dataclass
class Contact:
    """Contact data from HubSpot."""
    id: str
    email: str
    properties: Dict[str, Any]
    list_memberships: Set[str]  # HubSpot list IDs this contact belongs to


@dataclass
class MailchimpMember:
    """Mailchimp member current state."""
    email: str
    status: str  # subscribed, unsubscribed, cleaned, pending, archived
    tags: List[str]  # Active tags
    merge_fields: Dict[str, Any]


class PlannerEngine:
    """
    Generates deterministic operations plan for Primary Sync (HubSpot → Mailchimp).
    
    Enforces all V1 invariants with zero tolerance.
    """
    
    def __init__(self, config: "V2Config"):
        """Initialize planner with config."""
        self.config = config
        self.operations = []
        self.stats = {
            "contacts_processed": 0,
            "contacts_excluded": 0,
            "upserts_planned": 0,
            "tags_added": 0,
            "tags_removed": 0,
            "archives_planned": 0
        }
        
        # Track which contacts we've already tagged (INV-003: single-tag enforcement)
        self.tagged_contacts: Set[str] = set()
        
        # Managed tags (tags created by HubSpot sync)
        self.managed_tags_pattern = f"{config.sync.tag_prefix}hs_"
    
    def generate_plan(
        self,
        contacts: List[Contact],
        mc_members: Dict[str, MailchimpMember],
        list_id_to_name: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Generate operations plan for contacts.
        
        Args:
            contacts: List of HubSpot contacts with list memberships
            mc_members: Current Mailchimp members by email (lowercase)
            list_id_to_name: Map of HubSpot list ID → name
        
        Returns:
            {
                "version": "2.0",
                "generated_at": ISO8601,
                "config_file": path,
                "config_hash": hash,
                "summary": {...},
                "operations": [...]
            }
        """
        self.operations = []
        self.stats = {
            "contacts_processed": 0,
            "contacts_excluded": 0,
            "upserts_planned": 0,
            "tags_added": 0,
            "tags_removed": 0,
            "archives_planned": 0
        }
        self.tagged_contacts = set()
        
        # INV-001: Build exclusion matrix from config
        exclusion_matrix = self._build_exclusion_matrix()
        
        # Phase 1: Process each contact through import streams
        for contact in contacts:
            self.stats["contacts_processed"] += 1
            
            # INV-002: Skip compliance list members (never sync)
            if self._is_compliance_excluded(contact):
                self.stats["contacts_excluded"] += 1
                continue
            
            # Determine which lists contact is eligible for (after exclusions)
            eligible_lists = self._apply_exclusion_matrix(contact, exclusion_matrix)
            
            if not eligible_lists:
                self.stats["contacts_excluded"] += 1
                continue
            
            # Get current Mailchimp state
            mc_member = mc_members.get(contact.email.lower())
            
            # Plan upsert operation
            self._plan_upsert(contact, mc_member)
            
            # Plan tagging (INV-003: single-tag, first-wins)
            self._plan_tagging(contact, mc_member, eligible_lists, list_id_to_name)
            
            # Plan ORI_LISTS update (INV-004: source tracking)
            self._plan_ori_lists_update(contact, eligible_lists)
        
        # Phase 2: Plan tag removals (orphaned tags)
        self._plan_tag_removals(contacts, mc_members, list_id_to_name)
        
        # Phase 3: Plan archival for fully-orphaned managed contacts
        self._plan_archival(contacts, mc_members)
        
        # Sort operations for determinism (email then op type)
        self.operations.sort(key=lambda op: (op.get("email", ""), op["type"]))
        
        # Build plan document
        from corev2.config import compute_config_hash
        plan = {
            "version": "2.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config_file": "corev2/config/defaults.yaml",
            "config_hash": compute_config_hash(self.config),
            "summary": {
                "total_operations": len(self.operations),
                "contacts_processed": self.stats["contacts_processed"],
                "operations_by_type": self._count_operations_by_type()
            },
            "operations": self.operations
        }
        
        return plan
    
    def _build_exclusion_matrix(self) -> Dict[str, Set[str]]:
        """Build exclusion matrix for each list from config (INV-001)."""
        matrix = {}
        em = self.config.exclusion_matrix
        
        # GROUP 1: General Marketing
        for list_id in em.general_marketing.lists:
            matrix[list_id] = set(em.general_marketing.exclude)
        
        # GROUP 2: Special Campaigns
        for list_id in em.special_campaigns.lists:
            matrix[list_id] = set(em.special_campaigns.exclude)
        
        # GROUP 3: Manual Override
        for list_id in em.manual_override.lists:
            matrix[list_id] = set(em.manual_override.exclude)
        
        return matrix
    
    def _is_compliance_excluded(self, contact: Contact) -> bool:
        """INV-002: Check if contact is in compliance lists (762, 773).
        
        NOTE: Lists 762 and 773 are DYNAMIC LISTS - auto-managed by HubSpot based on criteria.
        We check membership but NEVER manually add/remove contacts from these lists.
        """
        compliance_lists = {"762", "773"}  # DYNAMIC: 762=Opted Out, 773=Manual Disengagement
        return bool(contact.list_memberships & compliance_lists)
    
    def _apply_exclusion_matrix(
        self,
        contact: Contact,
        exclusion_matrix: Dict[str, Set[str]]
    ) -> List[str]:
        """
        Apply exclusion matrix to determine eligible lists for contact.
        
        Args:
            contact: Contact with list memberships
            exclusion_matrix: Map of list_id → exclusion_list_ids
        
        Returns:
            List of eligible list IDs (after exclusions applied)
        """
        eligible = []
        
        for list_id in contact.list_memberships:
            if list_id not in exclusion_matrix:
                continue  # Not a sync list
            
            # Check if contact is in any exclusion lists for this list
            exclusion_lists = exclusion_matrix[list_id]
            if contact.list_memberships & exclusion_lists:
                continue  # Excluded
            
            eligible.append(list_id)
        
        return eligible
    
    def _plan_upsert(self, contact: Contact, mc_member: Optional[MailchimpMember]):
        """
        Plan upsert operation (INV-007: force subscribe).
        
        Never resubscribe unsubscribed/cleaned members (only update merge_fields).
        """
        # INV-007: Force subscribe unless unsubscribed/cleaned
        status_if_new = "subscribed"
        
        # Extract merge fields from contact properties
        merge_fields = {}
        for key, value in contact.properties.items():
            if value is not None:
                merge_fields[key.upper()] = str(value)
        
        operation = {
            "type": "upsert_mc_member",
            "list_id": self.config.mailchimp.audience_id,
            "email": contact.email,
            "data": {
                "email_address": contact.email,
                "status_if_new": status_if_new,
                "merge_fields": merge_fields
            },
            "contact_id": contact.id,
            "reason": "Sync HubSpot contact to Mailchimp"
        }
        
        self.operations.append(operation)
        self.stats["upserts_planned"] += 1
    
    def _plan_tagging(
        self,
        contact: Contact,
        mc_member: Optional[MailchimpMember],
        eligible_lists: List[str],
        list_id_to_name: Dict[str, str]
    ):
        """
        Plan tagging operations (INV-003: single-tag enforcement, first-wins).
        
        If contact already has a HubSpot-managed tag, skip additional tags.
        """
        # INV-003: Check if contact already tagged (first-wins)
        if contact.email.lower() in self.tagged_contacts:
            return
        
        # Check if MC member already has a managed tag
        if mc_member:
            for tag in mc_member.tags:
                if self._is_managed_tag(tag):
                    # Already has a managed tag, mark as tagged and skip
                    self.tagged_contacts.add(contact.email.lower())
                    return
        
        # Apply first eligible list's tag
        if eligible_lists:
            first_list = eligible_lists[0]
            list_name = list_id_to_name.get(first_list, first_list)
            tag_name = f"{self.config.sync.tag_prefix}hs_{list_name}"
            
            operation = {
                "type": "apply_mc_tag",
                "list_id": self.config.mailchimp.audience_id,
                "email": contact.email,
                "tag_name": tag_name,
                "contact_id": contact.id,
                "reason": f"First-wins tag from list {list_name}"
            }
            
            self.operations.append(operation)
            self.stats["tags_added"] += 1
            self.tagged_contacts.add(contact.email.lower())
    
    def _plan_ori_lists_update(self, contact: Contact, eligible_lists: List[str]):
        """
        Plan ORI_LISTS update (INV-004: single-value source tracking).
        
        Format: Single list ID string (first eligible list).
        """
        if not eligible_lists:
            return
        
        # INV-004: Single-value string (first eligible list)
        ori_lists_value = eligible_lists[0]
        
        operation = {
            "type": "update_hs_property",
            "contact_id": contact.id,
            "property": self.config.sync.ori_lists_field,
            "value": ori_lists_value,
            "reason": "Track source list for anti-remarketing"
        }
        
        self.operations.append(operation)
    
    def _plan_tag_removals(
        self,
        contacts: List[Contact],
        mc_members: Dict[str, MailchimpMember],
        list_id_to_name: Dict[str, str]
    ):
        """Plan tag removal for contacts no longer in corresponding lists."""
        # Build set of emails still in HubSpot
        hs_emails = {c.email.lower() for c in contacts}
        
        # Build expected tags for each email
        expected_tags = {}
        for contact in contacts:
            eligible_lists = [lid for lid in contact.list_memberships if not self._is_compliance_excluded(contact)]
            if eligible_lists:
                first_list = list(contact.list_memberships)[0]
                list_name = list_id_to_name.get(first_list, first_list)
                expected_tags[contact.email.lower()] = f"{self.config.sync.tag_prefix}hs_{list_name}"
        
        # Check MC members for orphaned tags
        for email, mc_member in mc_members.items():
            if email not in hs_emails:
                continue  # Will be handled by archival
            
            expected_tag = expected_tags.get(email)
            for tag in mc_member.tags:
                if self._is_managed_tag(tag) and tag != expected_tag:
                    operation = {
                        "type": "remove_mc_tag",
                        "list_id": self.config.mailchimp.audience_id,
                        "email": mc_member.email,
                        "tag_name": tag,
                        "contact_id": None,
                        "reason": "Tag orphaned (contact no longer in corresponding list)"
                    }
                    self.operations.append(operation)
                    self.stats["tags_removed"] += 1
    
    def _plan_archival(
        self,
        contacts: List[Contact],
        mc_members: Dict[str, MailchimpMember]
    ):
        """
        Plan archival for fully-orphaned managed contacts (INV-006).
        
        Archive ONLY if:
        - Not in any HubSpot list
        - Has ONLY managed tags (no manual/custom tags)
        - Not in exempt_tags list
        """
        hs_emails = {c.email.lower() for c in contacts}
        
        for email, mc_member in mc_members.items():
            if email in hs_emails:
                continue  # Still in HubSpot
            
            # Check if has only managed tags
            has_only_managed = all(self._is_managed_tag(tag) for tag in mc_member.tags)
            if not has_only_managed:
                continue  # Has manual/custom tags, preserve
            
            # Check exempt tags (INV-006)
            if any(tag in self.config.archival.exempt_tags for tag in mc_member.tags):
                continue  # Exempt from archival
            
            operation = {
                "type": "archive_mc_member",
                "list_id": self.config.mailchimp.audience_id,
                "email": mc_member.email,
                "contact_id": None,
                "reason": "Fully orphaned (no HubSpot lists, only managed tags)"
            }
            self.operations.append(operation)
            self.stats["archives_planned"] += 1
    
    def _is_managed_tag(self, tag: str) -> bool:
        """Check if tag is managed by HubSpot sync."""
        return tag.startswith(self.managed_tags_pattern)
    
    def _count_operations_by_type(self) -> Dict[str, int]:
        """Count operations by type."""
        counts = {}
        for op in self.operations:
            op_type = op["type"]
            counts[op_type] = counts.get(op_type, 0) + 1
        return counts
