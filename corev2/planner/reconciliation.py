"""
Archival Reconciliation Module

Scans Mailchimp members that have source group tags but don't exist in any HubSpot list.
Generates archive_mc_member operations for orphaned members.

Implements INV-006: Smart archival preservation (respects exempt tags and patterns).
"""

import logging
from typing import Dict, Set, List, Any, Optional
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of archival reconciliation scan."""
    total_mailchimp_members: int
    active_hubspot_contacts: int
    orphaned_members: int
    exempt_members: int
    archive_operations: List[Dict[str, Any]]


class ArchivalReconciliation:
    """
    Handles archival reconciliation between Mailchimp and HubSpot.
    
    Scans Mailchimp for members with source tags that no longer exist in HubSpot lists.
    """
    
    def __init__(self, mc_client, config, max_archive_per_run: int = 25):
        """
        Initialize reconciliation engine.
        
        Args:
            mc_client: MailchimpClient instance
            config: V2Config instance
            max_archive_per_run: Safety limit on archival operations per run
        """
        self.mc_client = mc_client
        self.config = config
        self.max_archive_per_run = max_archive_per_run
        
        # Build source tags dynamically from config (all list tags)
        self.source_tags = set()
        for group_name, list_configs in self.config.hubspot.lists.items():
            for list_config in list_configs:
                self.source_tags.add(list_config.tag)
        
        logger.info(f"ArchivalReconciliation initialized (max_archive_per_run={max_archive_per_run})")
        logger.info(f"Tracking source tags: {self.source_tags}")
    
    def _is_exempt_from_archival(self, member: Dict[str, Any]) -> bool:
        """
        Check if member is exempt from archival (INV-006).
        
        Args:
            member: Mailchimp member dict with tags
        
        Returns:
            True if member should be preserved (not archived)
        """
        member_tags = set(member.get("tags", []))
        
        # Check exempt tags
        exempt_tags = set(self.config.archival.exempt_tags)
        if member_tags.intersection(exempt_tags):
            logger.debug(f"Member {member.get('email_address')} exempt: has exempt tag")
            return True
        
        # Check preservation patterns (regex)
        for pattern in self.config.archival.preservation_patterns:
            if any(re.match(pattern, tag) for tag in member_tags):
                logger.debug(f"Member {member.get('email_address')} exempt: tag matches pattern {pattern}")
                return True
        
        return False
    
    async def scan_for_orphans(
        self,
        active_hubspot_emails: Set[str],
        dry_run: bool = True
    ) -> ReconciliationResult:
        """
        Scan Mailchimp for orphaned members (have source tags but not in HubSpot).
        
        Args:
            active_hubspot_emails: Set of emails currently in synced HubSpot lists
            dry_run: If True, only report (no operations); if False, generate archive operations
        
        Returns:
            ReconciliationResult with statistics and archive operations
        """
        logger.info("Running archival reconciliation...")
        logger.info(f"  Comparing Mailchimp audience vs {len(active_hubspot_emails)} active HubSpot contacts...")
        
        orphaned_members = []
        exempt_count = 0
        scanned_count = 0
        
        # Scan all Mailchimp members
        # NOTE: In production, consider filtering by tag to reduce API calls
        async for member in self.mc_client.get_all_members():
            scanned_count += 1
            
            if scanned_count % 500 == 0:
                logger.info(f"  Scanned {scanned_count} Mailchimp members...")
            
            email = member.get("email_address", "").lower()
            member_tags = set(member.get("tags", []))
            status = member.get("status")
            
            # Only consider members with source tags (managed by our system)
            has_source_tag = bool(member_tags.intersection(self.source_tags))
            
            if not has_source_tag:
                continue  # Not managed by us, skip
            
            # Check if member is orphaned (not in any HubSpot list)
            is_orphaned = email not in active_hubspot_emails
            
            if not is_orphaned:
                continue  # Still active in HubSpot, skip
            
            # Check exemptions (INV-006)
            if self._is_exempt_from_archival(member):
                exempt_count += 1
                continue
            
            # Check if already archived
            if status == "archived":
                logger.debug(f"Member {email} already archived, skipping")
                continue
            
            # This is an orphan candidate for archival
            orphaned_members.append({
                "email": email,
                "status": status,
                "tags": list(member_tags)
            })
        
        logger.info(f"\n✓ Archival Reconciliation Complete:")
        logger.info(f"  • Mailchimp members scanned: {scanned_count}")
        logger.info(f"  • Active HubSpot contacts: {len(active_hubspot_emails)}")
        logger.info(f"  • Orphaned members found: {len(orphaned_members)}")
        logger.info(f"  • Exempt from archival: {exempt_count}")
        
        # Generate archive operations (respecting max_archive_per_run limit)
        # IMPORTANT: Untag first, then archive
        archive_operations = []
        
        if not dry_run:
            for member in orphaned_members[:self.max_archive_per_run]:
                # Find which source tags to remove
                tags_to_remove = list(set(member["tags"]).intersection(self.source_tags))
                
                # Step 1: Unsubscribe if currently subscribed (GDPR compliance)
                # Must match status across both platforms before archival
                if member["status"] == "subscribed":
                    archive_operations.append({
                        "type": "unsubscribe_mc_member",
                        "email": member["email"],
                        "reason": "opted_out_in_hubspot"
                    })
                
                # Step 2: Remove source tags (clean untag)
                if tags_to_remove:
                    archive_operations.append({
                        "type": "remove_mc_tag",
                        "email": member["email"],
                        "tags": tags_to_remove
                    })
                
                # Step 3: Archive (removes from active audience)
                archive_operations.append({
                    "type": "archive_mc_member",
                    "email": member["email"]
                })
            
            if len(orphaned_members) > self.max_archive_per_run:
                logger.info(
                    f"  • Archival safety limit: {self.max_archive_per_run} / {len(orphaned_members)} orphans "
                    f"(remaining: {len(orphaned_members) - self.max_archive_per_run})"
                )
        
        logger.info(f"  • Archive operations generated: {len(archive_operations)}")
        
        return ReconciliationResult(
            total_mailchimp_members=scanned_count,
            active_hubspot_contacts=len(active_hubspot_emails),
            orphaned_members=len(orphaned_members),
            exempt_members=exempt_count,
            archive_operations=archive_operations
        )
