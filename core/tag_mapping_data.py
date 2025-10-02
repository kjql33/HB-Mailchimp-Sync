#!/usr/bin/env python3
"""
🏷️ TAG MAPPING DATA - Smart Archival System
=============================================

This file contains comprehensive tag mappings for the smart archival system.
It defines which Mailchimp tags correspond to HubSpot lists and how they should
be handled during archival operations.

⚠️ IMPORTANT: This file is committed to git and read during every sync operation
for maximum performance and reliability.
"""

from typing import Dict, List, Set, Optional

# =============================================================================
# 🎯 HUBSPOT LIST TO MAILCHIMP TAG MAPPING
# =============================================================================

# Primary tag mappings - each HubSpot list creates contacts with these tags
HUBSPOT_TO_MAILCHIMP_TAG_MAP = {
    # GROUP 1: General Marketing Campaigns
    "718": "General",           # General marketing → "General" tag
    "719": "Recruitment",       # Recruitment campaigns → "Recruitment" tag  
    "720": "Competition",       # Competition campaigns → "Competition" tag
    "751": "Directors",         # Directors targeting → "Directors" tag
    
    # GROUP 2: Demo Campaigns  
    "872": "Demo",              # Demo list → "Demo" tag (already exists in Mailchimp)
    
    # GROUP 3: Manual Override (appears as General)
    "784": "General",           # Manual override → appears as "General" tag
}

# Reverse mapping - which HubSpot lists generate each tag
MAILCHIMP_TAG_TO_HUBSPOT_LISTS = {
    "General": ["718", "784"],          # Both General list and Manual override
    "Recruitment": ["719"],
    "Competition": ["720"], 
    "Directors": ["751"],
    "Demo": ["872"],                    # Demo list
}

# =============================================================================
# 🔍 SMART ARCHIVAL LOGIC CONFIGURATION  
# =============================================================================

# Tags that correspond to active HubSpot lists and should be managed
MANAGED_MAILCHIMP_TAGS = set(MAILCHIMP_TAG_TO_HUBSPOT_LISTS.keys())

# Tags that should be preserved (manually created, not from HubSpot)
# These will NEVER be archived regardless of contact status
PRESERVED_TAG_PATTERNS = [
    "Manual_",          # Any tag starting with "Manual_" 
    "Custom_",          # Any tag starting with "Custom_"
    "Event_",           # Any tag starting with "Event_"
    "Campaign_",        # Any tag starting with "Campaign_"
    "Test_",            # Any tag starting with "Test_"
]

# Contacts with these tags will be completely ignored by archival system
ARCHIVAL_EXEMPT_TAGS = [
    "VIP",              # VIP contacts - never archive
    "Founder",          # Company founders - never archive  
    "Partner",          # Business partners - never archive
    "Employee",         # Company employees - never archive
    "Investor",         # Investors - never archive
    "Manual_Addition",  # Manually added contacts
]

# =============================================================================
# 🧮 ARCHIVAL DECISION MATRIX
# =============================================================================

class SmartArchivalDecision:
    """
    Comprehensive decision engine for tag-aware archival operations.
    """
    
    @staticmethod
    def should_preserve_contact(email: str, tags: List[str], hubspot_status: Dict[str, any]) -> Dict[str, any]:
        """
        Determine if a contact should be preserved based on comprehensive analysis.
        
        Args:
            email: Contact email address
            tags: List of Mailchimp tags for this contact  
            hubspot_status: Dict containing HubSpot list membership info
            
        Returns:
            Dict with decision, reason, and metadata
        """
        decision_data = {
            "preserve": False,
            "reason": "",
            "archival_category": "",
            "tags_analyzed": tags,
            "hubspot_lists": hubspot_status.get("member_of_lists", []),
            "managed_tags": [],
            "exempt_tags": [],
            "manual_tags": []
        }
        
        # Step 1: Check for archival-exempt tags (VIP, Partner, etc.)
        exempt_tags = [tag for tag in tags if tag in ARCHIVAL_EXEMPT_TAGS]
        if exempt_tags:
            decision_data.update({
                "preserve": True,
                "reason": f"Contact has exempt tags: {exempt_tags}",
                "archival_category": "exempt_tagged",
                "exempt_tags": exempt_tags
            })
            return decision_data
        
        # Step 2: Check for preserved tag patterns (Manual_, Custom_, etc.)
        manual_tags = []
        for tag in tags:
            for pattern in PRESERVED_TAG_PATTERNS:
                if tag.startswith(pattern):
                    manual_tags.append(tag)
        
        if manual_tags:
            decision_data.update({
                "preserve": True, 
                "reason": f"Contact has manual tags: {manual_tags}",
                "archival_category": "manual_tagged",
                "manual_tags": manual_tags
            })
            return decision_data
        
        # Step 3: Check if contact has NO managed tags (completely manual)
        managed_tags = [tag for tag in tags if tag in MANAGED_MAILCHIMP_TAGS]
        if not managed_tags:
            decision_data.update({
                "preserve": True,
                "reason": "Contact has no HubSpot-managed tags (manual addition)",
                "archival_category": "unmanaged_contact"
            })
            return decision_data
        
        # Step 4: Analyze managed tags vs HubSpot list membership
        decision_data["managed_tags"] = managed_tags
        
        # Check each managed tag against HubSpot list membership
        orphaned_tags = []
        valid_tags = []
        
        for tag in managed_tags:
            # Get HubSpot lists that should have this tag
            corresponding_lists = MAILCHIMP_TAG_TO_HUBSPOT_LISTS.get(tag, [])
            
            # Check if contact is in ANY of the corresponding HubSpot lists
            is_in_corresponding_list = any(
                list_id in hubspot_status.get("member_of_lists", []) 
                for list_id in corresponding_lists
            )
            
            if is_in_corresponding_list:
                valid_tags.append(tag)
            else:
                orphaned_tags.append(tag)
        
        # Step 5: Make final decision
        if orphaned_tags and not valid_tags:
            # All managed tags are orphaned - archive the contact
            decision_data.update({
                "preserve": False,
                "reason": f"All managed tags orphaned: {orphaned_tags}",
                "archival_category": "fully_orphaned",
                "orphaned_tags": orphaned_tags
            })
        elif orphaned_tags and valid_tags:
            # Mixed situation - preserve contact but log for review
            decision_data.update({
                "preserve": True,
                "reason": f"Mixed status: valid tags {valid_tags}, orphaned {orphaned_tags}",
                "archival_category": "partially_orphaned",
                "valid_tags": valid_tags,
                "orphaned_tags": orphaned_tags
            })
        else:
            # All managed tags are valid - preserve contact
            decision_data.update({
                "preserve": True,
                "reason": f"All managed tags valid: {valid_tags}", 
                "archival_category": "fully_valid",
                "valid_tags": valid_tags
            })
        
        return decision_data
    
    @staticmethod
    def get_archival_summary(contacts_analyzed: List[Dict]) -> Dict[str, any]:
        """
        Generate comprehensive summary of archival decisions.
        """
        summary = {
            "total_contacts": len(contacts_analyzed),
            "preserved_count": 0,
            "archived_count": 0,
            "categories": {},
            "preserved_reasons": {},
            "archived_reasons": {},
        }
        
        for contact in contacts_analyzed:
            category = contact.get("archival_category", "unknown")
            
            if category not in summary["categories"]:
                summary["categories"][category] = 0
            summary["categories"][category] += 1
            
            if contact.get("preserve", False):
                summary["preserved_count"] += 1
                reason = contact.get("reason", "unknown")
                summary["preserved_reasons"][reason] = summary["preserved_reasons"].get(reason, 0) + 1
            else:
                summary["archived_count"] += 1
                reason = contact.get("reason", "unknown")
                summary["archived_reasons"][reason] = summary["archived_reasons"].get(reason, 0) + 1
        
        return summary

# =============================================================================
# 🎯 INTEGRATION FUNCTIONS
# =============================================================================

def get_managed_tags_for_list(hubspot_list_id: str) -> List[str]:
    """
    Get all Mailchimp tags that should be created by a specific HubSpot list.
    
    Args:
        hubspot_list_id: HubSpot list ID (e.g., "718")
        
    Returns:
        List of Mailchimp tags this list should create
    """
    return [HUBSPOT_TO_MAILCHIMP_TAG_MAP.get(hubspot_list_id, f"List_{hubspot_list_id}")]

def get_hubspot_lists_for_tag(mailchimp_tag: str) -> List[str]:
    """
    Get all HubSpot lists that should contain contacts with a specific Mailchimp tag.
    
    Args:
        mailchimp_tag: Mailchimp tag name (e.g., "General")
        
    Returns:
        List of HubSpot list IDs that should contain these contacts
    """
    return MAILCHIMP_TAG_TO_HUBSPOT_LISTS.get(mailchimp_tag, [])

def is_managed_tag(tag: str) -> bool:
    """
    Check if a tag is managed by the HubSpot sync system.
    
    Args:
        tag: Mailchimp tag name
        
    Returns:
        True if tag is managed by sync system, False if manual/custom
    """
    return tag in MANAGED_MAILCHIMP_TAGS

def should_preserve_by_pattern(tags: List[str]) -> bool:
    """
    Check if any tags match preserved patterns.
    
    Args:
        tags: List of Mailchimp tags
        
    Returns:
        True if any tag matches preservation patterns
    """
    for tag in tags:
        for pattern in PRESERVED_TAG_PATTERNS:
            if tag.startswith(pattern):
                return True
        if tag in ARCHIVAL_EXEMPT_TAGS:
            return True
    return False

# =============================================================================
# 🔧 CONFIGURATION VALIDATION
# =============================================================================

def validate_tag_mappings() -> Dict[str, any]:
    """
    Validate tag mapping configuration for consistency.
    
    Returns:
        Validation results with any issues found
    """
    issues = []
    warnings = []
    
    # Check for orphaned HubSpot lists (no tag mapping)
    from .config import HUBSPOT_LIST_IDS
    
    unmapped_lists = []
    for list_id in HUBSPOT_LIST_IDS:
        if list_id not in HUBSPOT_TO_MAILCHIMP_TAG_MAP:
            unmapped_lists.append(list_id)
    
    if unmapped_lists:
        issues.append(f"HubSpot lists without tag mapping: {unmapped_lists}")
    
    # Check for tag consistency
    for tag, lists in MAILCHIMP_TAG_TO_HUBSPOT_LISTS.items():
        for list_id in lists:
            expected_tag = HUBSPOT_TO_MAILCHIMP_TAG_MAP.get(list_id)
            if expected_tag != tag:
                issues.append(f"Inconsistent mapping: List {list_id} maps to '{expected_tag}' but tag '{tag}' references list {list_id}")
    
    # Check for duplicate tag assignments
    tag_counts = {}
    for list_id, tag in HUBSPOT_TO_MAILCHIMP_TAG_MAP.items():
        if tag not in tag_counts:
            tag_counts[tag] = []
        tag_counts[tag].append(list_id)
    
    for tag, lists in tag_counts.items():
        if len(lists) > 1:
            if tag != "General":  # General tag is allowed to have multiple sources (784 override)
                warnings.append(f"Tag '{tag}' is generated by multiple lists: {lists}")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "mapped_lists": len(HUBSPOT_TO_MAILCHIMP_TAG_MAP),
        "managed_tags": len(MANAGED_MAILCHIMP_TAGS),
        "exempt_tags": len(ARCHIVAL_EXEMPT_TAGS),
        "preserved_patterns": len(PRESERVED_TAG_PATTERNS)
    }

if __name__ == "__main__":
    # Quick validation when run directly
    results = validate_tag_mappings()
    print("🏷️ Tag Mapping Validation Results:")
    print(f"✅ Valid: {results['valid']}")
    if results['issues']:
        print("❌ Issues:")
        for issue in results['issues']:
            print(f"  • {issue}")
    if results['warnings']:
        print("⚠️ Warnings:")  
        for warning in results['warnings']:
            print(f"  • {warning}")
    print(f"📊 Statistics: {results['mapped_lists']} lists, {results['managed_tags']} tags")
