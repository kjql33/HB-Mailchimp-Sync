"""Primary sync planner - generates operations_plan.json."""

import asyncio
import logging
from typing import Dict, List, Set, Any, Optional
from datetime import datetime
from corev2.config.schema import V2Config, ExclusionMatrixGroupConfig
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mailchimp_client import MailchimpClient

logger = logging.getLogger(__name__)


class SyncPlanner:
    """
    Dry-run planner that generates operations_plan.json.
    
    Reads HubSpot list members, applies business rules, and produces
    deterministic operation plan with no mutations.
    """
    
    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MailchimpClient):
        """
        Initialize planner.
        
        Args:
            config: Validated V2Config
            hs_client: HubSpot API client
            mc_client: Mailchimp API client
        """
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client
        # INV-002: Compliance lists - DYNAMIC LISTS auto-managed by HubSpot, NEVER manually modify
        self.compliance_lists = {"762", "773"}  # 762: Opted Out (auto-populated), 773: Manual Disengagement
    
    def _apply_exclusion_matrix(
        self,
        contact_list_ids: Set[str],
        group_config: ExclusionMatrixGroupConfig
    ) -> bool:
        """
        Check if contact should be excluded based on exclusion matrix.
        
        Args:
            contact_list_ids: Set of list IDs the contact is in
            group_config: Exclusion matrix group configuration
        
        Returns:
            True if contact should be excluded, False otherwise
        """
        # Contact is excluded if in ANY of the exclusion lists
        for exclude_list_id in group_config.exclude:
            if exclude_list_id in contact_list_ids:
                return True
        return False
    
    def _determine_target_tag(
        self,
        contact_list_ids: Set[str],
        email: str
    ) -> Optional[List[str]]:
        """
        Determine target tags based on exclusion matrix (INV-004: Single-tag enforcement).
        
        Priority: general_marketing → special_campaigns → manual_override
        NEW: Returns list of tags (primary + additional) from list config.
        
        Args:
            contact_list_ids: Set of list IDs the contact is in
            email: Contact email for logging
        
        Returns:
            List of tags [primary, additional...] from first matching list, or None if excluded from all groups
        """
        # Check each group in priority order
        for group_name in ["general_marketing", "special_campaigns", "manual_override"]:
            group_config = getattr(self.config.exclusion_matrix, group_name)
            
            # Check each list in this group (first match wins)
            for list_id in group_config.lists:
                if list_id in contact_list_ids:
                    # Check if excluded
                    excluded = self._apply_exclusion_matrix(contact_list_ids, group_config)
                    
                    if not excluded:
                        # Find the list config to get its tags
                        for hs_group_name, list_configs in self.config.hubspot.lists.items():
                            for list_config in list_configs:
                                if list_config.id == list_id:
                                    all_tags = [list_config.tag] + list_config.additional_tags
                                    logger.debug(f"Contact {email} matched list {list_id} ({list_config.name}) → tags: {all_tags}")
                                    return all_tags
                        
                        # Fallback to group name if no tag found (shouldn't happen)
                        logger.warning(f"No tag found for list {list_id}, falling back to group name: {group_name}")
                        return [group_name]
                    else:
                        logger.debug(f"Contact {email} excluded from {group_name}")
                        # Continue to next list
        
        return None
    
    def _check_list_exclusion_rules(
        self,
        target_list_id: str,
        contact_list_ids: Set[str]
    ) -> bool:
        """
        Check list exclusion rules (anti-remarketing).
        
        Args:
            target_list_id: Target HubSpot list ID
            contact_list_ids: Contact's current list memberships
        
        Returns:
            True if contact should be excluded due to list exclusion rules
        """
        excluded_lists = self.config.list_exclusion_rules.get(target_list_id, [])
        
        for excluded_list_id in excluded_lists:
            if excluded_list_id in contact_list_ids:
                return True
        
        return False
    
    async def generate_plan(
        self,
        contact_limit: Optional[int] = None,
        only_email: Optional[str] = None,
        only_vid: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate operations plan (dry-run - no mutations).
        
        Args:
            contact_limit: Optional limit on number of contacts to process
            only_email: If set, only process contact with this email (deterministic targeting)
            only_vid: If set, only process contact with this VID (deterministic targeting)
        
        Returns:
            operations_plan dict with summary + per-contact operations
        """
        logger.info("Starting plan generation...")
        
        if only_email and only_vid:
            raise ValueError("Cannot specify both --only-email and --only-vid")
        
        plan = {
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "config_hash": "",  # Will be set by CLI
                "contact_limit": contact_limit or 0,
                "run_mode": self.config.safety.run_mode.value,
                "filter_email": only_email,
                "filter_vid": only_vid,
            },
            "summary": {
                "total_contacts_scanned": 0,
                "contacts_with_operations": 0,
                "operations_by_type": {},
                "invariants_checked": {
                    "INV-002": "Compliance lists never synced",
                    "INV-004": "Single-tag enforcement",
                    "INV-007": "List exclusion rules"
                }
            },
            "operations": []
        }
        
        # Scan all HubSpot lists in exclusion_matrix
        all_list_ids = set()
        for group_name in ["general_marketing", "special_campaigns", "manual_override"]:
            group_config = getattr(self.config.exclusion_matrix, group_name)
            all_list_ids.update(group_config.lists)
        
        # CRITICAL: Also scan exclusion lists to detect contacts that should be excluded
        exclusion_list_ids = set()
        exclusion_list_ids.update(self.config.hubspot.exclusions.critical)
        exclusion_list_ids.update(self.config.hubspot.exclusions.active_deals)
        exclusion_list_ids.update(self.config.hubspot.exclusions.exit)
        
        logger.info(f"Scanning {len(all_list_ids)} sync lists: {sorted(all_list_ids)}")
        logger.info(f"Scanning {len(exclusion_list_ids)} exclusion lists: {sorted(exclusion_list_ids)}")
        
        # Also scan supplemental tag lists (these are NOT synced themselves)
        supplemental_list_ids = set()
        for supp_config in self.config.hubspot.supplemental_tags:
            supplemental_list_ids.add(supp_config.list_id)
        
        if supplemental_list_ids:
            logger.info(f"Scanning {len(supplemental_list_ids)} supplemental tag lists: {sorted(supplemental_list_ids)}")
        
        # Combine for complete list membership detection
        all_lists_to_scan = all_list_ids.union(exclusion_list_ids).union(supplemental_list_ids)
        
        # Collect contacts from all lists
        contacts_by_email = {}
        
        for list_id in sorted(all_lists_to_scan):
            logger.info(f"Fetching members from list {list_id}...")
            count = 0
            
            try:
                async for contact in self.hs_client.get_list_members(
                    list_id,
                    properties=["email", "firstname", "lastname", self.config.sync.ori_lists_field]
                ):
                    email = contact.get("email")
                    if not email:
                        continue
                    
                    # Aggregate list memberships
                    if email not in contacts_by_email:
                        contacts_by_email[email] = {
                            "vid": contact["vid"],
                            "email": email,
                            "properties": contact["properties"],
                            "list_ids": set()
                        }
                    
                    contacts_by_email[email]["list_ids"].add(list_id)
                    count += 1
                    
                    # Apply contact limit
                    if contact_limit and len(contacts_by_email) >= contact_limit:
                        logger.info(f"Reached contact limit ({contact_limit}), stopping scan")
                        break
                
                logger.info(f"  Found {count} contacts in list {list_id}")
            
            except Exception as e:
                logger.error(f"Error fetching list {list_id}: {e}")
                raise
            
            if contact_limit and len(contacts_by_email) >= contact_limit:
                break
        
        logger.info(f"Total unique contacts: {len(contacts_by_email)}")
        plan["summary"]["total_contacts_scanned"] = len(contacts_by_email)
        
        # IMPORTANT: Preserve full contact set for archival reconciliation BEFORE filtering
        all_contacts_by_email = contacts_by_email.copy()
        
        # Apply deterministic filtering if specified
        if only_email or only_vid:
            filtered = {}
            if only_email:
                if only_email in contacts_by_email:
                    filtered[only_email] = contacts_by_email[only_email]
                    logger.info(f"✓ Filtered to contact: {only_email}")
                else:
                    logger.warning(f"⚠ Contact not found: {only_email}")
            elif only_vid:
                for email, data in contacts_by_email.items():
                    if data["vid"] == only_vid:
                        filtered[email] = data
                        logger.info(f"✓ Filtered to contact VID: {only_vid} ({email})")
                        break
                if not filtered:
                    logger.warning(f"⚠ Contact VID not found: {only_vid}")
            
            contacts_by_email = filtered
        
        # Generate operations for each contact
        for email, contact_data in contacts_by_email.items():
            operations = await self._plan_contact_operations(
                email,
                contact_data["vid"],
                contact_data["list_ids"],
                contact_data["properties"]
            )
            
            if operations:
                plan["operations"].append({
                    "email": email,
                    "vid": contact_data["vid"],
                    "operations": operations
                })
                plan["summary"]["contacts_with_operations"] += 1
                
                # Count operations by type
                for op in operations:
                    op_type = op["type"]
                    plan["summary"]["operations_by_type"][op_type] = \
                        plan["summary"]["operations_by_type"].get(op_type, 0) + 1
        
        # Archival Reconciliation (if enabled)
        if self.config.safety.allow_archive:
            logger.info("Running archival reconciliation...")
            from corev2.planner.reconciliation import ArchivalReconciliation
            
            # CRITICAL: Use full contact set (before --only-email filtering)
            # Otherwise filtered runs will incorrectly mark active contacts as orphans!
            active_emails = set(all_contacts_by_email.keys())
            
            # CRITICAL: Remove contacts in EXCLUSION lists from active_emails
            # If contact in Mailchimp but now in exclusion list → should be archived
            # Track which contacts + their sync lists for later HubSpot cleanup
            excluded_contacts = {}  # {email: {"vid": int, "sync_list_ids": [str]}}
            excluded_count = 0
            
            for email in list(active_emails):
                contact_data = all_contacts_by_email[email]
                list_ids = contact_data["list_ids"]
                
                # Check if contact is in ANY exclusion list
                for group_name in ["general_marketing", "special_campaigns", "manual_override"]:
                    group_config = getattr(self.config.exclusion_matrix, group_name)
                    if self._apply_exclusion_matrix(list_ids, group_config):
                        active_emails.remove(email)
                        excluded_count += 1
                        
                        # Track sync lists this contact is in (need to remove from these)
                        sync_list_ids = [lid for lid in list_ids if lid in group_config.lists]
                        excluded_contacts[email] = {
                            "vid": contact_data["vid"],
                            "sync_list_ids": sync_list_ids
                        }
                        
                        logger.info(f"Contact {email} in exclusion list → removed from active set (will be archived if in Mailchimp)")
                        if sync_list_ids:
                            logger.info(f"  → Will be removed from HubSpot lists: {sync_list_ids}")
                        break
            
            if excluded_count > 0:
                logger.info(f"Removed {excluded_count} contacts in exclusion lists from active set")
            
            # Run reconciliation
            reconciler = ArchivalReconciliation(
                mc_client=self.mc_client,
                config=self.config,
                max_archive_per_run=self.config.archival.max_archive_per_run
            )
            
            recon_result = await reconciler.scan_for_orphans(
                active_hubspot_emails=active_emails,
                dry_run=False  # Generate operations
            )
            
            # Add archival operations to plan
            for archive_op in recon_result.archive_operations:
                email = archive_op["email"]
                
                # Check if this contact is being archived due to exclusion
                # If so, generate HubSpot list removal operations
                operations_list = [archive_op]
                
                if email in excluded_contacts:
                    # Generate removal operations for each sync list
                    vid = excluded_contacts[email]["vid"]
                    sync_list_ids = excluded_contacts[email]["sync_list_ids"]
                    
                    for list_id in sync_list_ids:
                        operations_list.append({
                            "type": "remove_hs_from_list",
                            "list_id": list_id,
                            "vid": vid,
                            "reason": "contact_in_exclusion_list"
                        })
                        logger.info(f"  → Generating HubSpot list removal: {email} from List {list_id}")
                    
                    plan["summary"]["operations_by_type"]["remove_hs_from_list"] = \
                        plan["summary"]["operations_by_type"].get("remove_hs_from_list", 0) + len(sync_list_ids)
                
                # Add as standalone contact entry
                plan["operations"].append({
                    "email": email,
                    "vid": excluded_contacts.get(email, {}).get("vid"),
                    "operations": operations_list
                })
                
                # Update summary
                plan["summary"]["contacts_with_operations"] += 1
                plan["summary"]["operations_by_type"]["archive_mc_member"] = \
                    plan["summary"]["operations_by_type"].get("archive_mc_member", 0) + 1
            
            # Add reconciliation stats to plan metadata
            plan["metadata"]["reconciliation"] = {
                "orphaned_members": recon_result.orphaned_members,
                "exempt_members": recon_result.exempt_members,
                "archive_operations_generated": len(recon_result.archive_operations)
            }
            
            logger.info(f"Reconciliation complete: {len(recon_result.archive_operations)} archive operations")
        
        logger.info(f"Plan complete: {plan['summary']['contacts_with_operations']} contacts with operations")
        return plan
    
    async def _plan_contact_operations(
        self,
        email: str,
        vid: int,
        list_ids: Set[str],
        properties: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Plan operations for a single contact.
        
        Args:
            email: Contact email
            vid: HubSpot VID
            list_ids: Set of HubSpot list IDs contact is in
            properties: Contact properties
        
        Returns:
            List of operation dicts
        """
        operations = []
        
        # INV-002: Skip compliance lists (should never happen due to config validation)
        if self.compliance_lists.intersection(list_ids):
            logger.warning(f"INVARIANT VIOLATION: Contact {email} in compliance lists {list_ids & self.compliance_lists}")
            return []
        
        # Determine target tags (single primary tag + optional additional tags for subdivisions)
        target_tags = self._determine_target_tag(list_ids, email)
        
        if not target_tags:
            # Contact excluded from all groups - no operations
            logger.debug(f"Contact {email} excluded from all groups")
            return []
        
        # Check for supplemental tags (contacts in both parent list and supplemental list)
        for supp_config in self.config.hubspot.supplemental_tags:
            # Check if contact is in BOTH the parent list AND the supplemental list
            if supp_config.parent_list_id in list_ids and supp_config.list_id in list_ids:
                # Add the supplemental tag if not already present
                if supp_config.tag not in target_tags:
                    target_tags.append(supp_config.tag)
                    logger.info(f"Contact {email} in both {supp_config.parent_list_id} and {supp_config.list_id} → adding supplemental tag '{supp_config.tag}'")
        
        # Fetch existing Mailchimp member to check current tags (INV-004 enforcement)
        # STRICT MODE: If Mailchimp read fails (non-404), skip contact rather than proceeding blindly
        # 404 is expected for new contacts and should NOT trigger strict mode skip
        existing_tags = []
        try:
            mc_member = await self.mc_client.get_member(email)
            existing_tags = mc_member.get("tags", [])
        except Exception as e:
            # Check if this is a 404 (contact not found) - expected for new contacts
            is_404 = "404" in str(e) or "not found" in str(e).lower()
            if is_404:
                logger.info(f"Contact {email} not found in Mailchimp (new contact, will be created)")
                existing_tags = []  # No existing tags for new contact
            else:
                # Non-404 error means Mailchimp API issue - STRICT MODE kicks in
                logger.error(f"Failed to fetch Mailchimp member {email} for INV-004 check: {e}")
                logger.error(f"STRICT MODE: Skipping contact {email} - cannot verify tag state")
                return []
        
        # Determine which source tags exist (tags managed by this system)
        # Build set of all possible source tags from config
        all_source_tags = set()
        for group_name, list_configs in self.config.hubspot.lists.items():
            for list_config in list_configs:
                all_source_tags.add(list_config.tag)
        
        # Add supplemental tags to the source tags set
        for supp_config in self.config.hubspot.supplemental_tags:
            all_source_tags.add(supp_config.tag)
        
        current_source_tags = [tag for tag in existing_tags if tag in all_source_tags]
        
        # INV-004a: First-tag priority - if contact already has a primary source tag, keep it
        # This prevents dual campaign enrollment when contact is added to multiple lists
        primary_tag = target_tags[0]  # First tag is always the primary campaign tag
        if current_source_tags:
            existing_tag = current_source_tags[0]  # First tag wins
            logger.info(f"Contact {email} already has source tag '{existing_tag}' - preserving (first-tag priority)")
            
            # Override target_tags to maintain existing primary tag (no campaign switch)
            # But still apply additional tags (subdivisions like T2)
            target_tags = [existing_tag] + target_tags[1:]
            tags_to_remove = []  # No tags to remove - keeping existing
        else:
            # INV-004: Single-tag enforcement - remove old source tags if contact moved to different list
            tags_to_remove = [tag for tag in current_source_tags if tag not in target_tags]
        
        # Check if contact should be archived based on archival rules
        # (Simplified: just checking if contact has no valid tag)
        # Real implementation would check exempt_tags, preservation_patterns, etc.
        
        # Extract property values (handle both v1 nested format and v3 flat format)
        def get_property_value(prop_name: str) -> str:
            prop = properties.get(prop_name, "")
            if isinstance(prop, dict):
                # v1 API format: {"value": "..."}
                return prop.get("value", "")
            else:
                # v3 API format: direct string value
                return str(prop) if prop else ""
        
        # Plan Mailchimp operations
        operations.append({
            "type": "upsert_mc_member",
            "email": email,
            "merge_fields": {
                "FNAME": get_property_value("firstname"),
                "LNAME": get_property_value("lastname")
            },
            "status_if_new": "subscribed"
        })
        
        # INV-004: Remove old source tags before applying new ones (single-tag enforcement)
        if tags_to_remove:
            logger.info(f"Contact {email} moved groups: removing old tags {tags_to_remove}, applying {target_tags}")
            operations.append({
                "type": "remove_mc_tag",
                "email": email,
                "tags": tags_to_remove
            })
        
        # Plan tag application (primary tag + additional subdivision tags)
        for tag in target_tags:
            operations.append({
                "type": "apply_mc_tag",
                "email": email,
                "tag": tag
            })
        
        # Plan ORI_LISTS update (INV-008) - only if enabled
        if self.config.safety.enable_hubspot_writes:
            ori_lists_value = ",".join(sorted(list_ids))
            operations.append({
                "type": "update_hs_property",
                "vid": vid,
                "property": self.config.sync.ori_lists_field,
                "value": ori_lists_value
            })
        
        return operations
