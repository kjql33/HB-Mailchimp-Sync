#!/usr/bin/env python3
"""
secondary_sync.py

Secondary sync engine for Mailchimp → HubSpot synchronization.
Processes exit-tagged contacts from Mailchimp marketing journeys and imports them
back to corresponding HubSpot lists with anti-remarketing controls.
"""

import os
import json
import time
import math
import logging
import hashlib
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from . import config
from . import notifications
from . import list_manager

# Import configuration values directly
from .config import (
    SECONDARY_SYNC_MAPPINGS, LIST_EXCLUSION_RULES, ENABLE_MAILCHIMP_ARCHIVAL,
    MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC, HUBSPOT_PRIVATE_TOKEN,
    SECONDARY_SYNC_MODE, SECONDARY_TEST_CONTACT_LIMIT
)

# Import source list tracking configuration
from .config import ORI_LISTS_FIELD

# Import notification functions from notifications module
from .notifications import notify_info, notify_warning, notify_error

# Set up logger
logger = logging.getLogger(__name__)


class MailchimpToHubSpotSync:
    """Handles secondary sync operations from Mailchimp to HubSpot"""
    
    def __init__(self):
        """Initialize the secondary sync engine"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'HubSpot-Mailchimp-Sync/2.0'
        })
        
        # API endpoints
        self.mailchimp_base_url = f"https://{config.MAILCHIMP_DC}.api.mailchimp.com/3.0"
        self.hubspot_base_url = "https://api.hubapi.com"
        
        # Stats tracking
        self.stats = {
            'contacts_processed': 0,
            'contacts_imported': 0,
            'contacts_archived': 0,
            'contacts_removed_from_source': 0,
            'errors': 0,
            'verifications_attempted': 0,
            'verifications_successful': 0,
            'verifications_failed': 0,
            'rollbacks_attempted': 0,
            'rollbacks_successful': 0,
            'rollbacks_failed': 0,
            'start_time': datetime.now()
        }
        
        # Raw data storage
        self.raw_data_dir = config.RAW_DATA_DIR
        os.makedirs(self.raw_data_dir, exist_ok=True)
        
        # Initialize list manager
        self.list_manager = list_manager.HubSpotListManager()
        
        # Phase 4: Rollback tracking
        self.rollback_journal = []  # Journal of all reversible operations
        self.operation_stack = []   # Stack for tracking operation order
        self.error_threshold = 0.3  # Rollback if >30% of operations fail
    
    def log(self, message: str, level: str = "INFO"):
        """Log message with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")
    
    def get_exit_tagged_contacts(self) -> Dict[str, List[Dict]]:
        """
        Fetch contacts from Mailchimp who have exit tags with pagination support for large lists.
        Phase 5: Enhanced with efficient batch processing and memory management.
        Returns: Dict mapping exit_tag -> list of contacts with that tag
        """
        self.log("🔍 Phase 5: Fetching exit-tagged contacts from Mailchimp with pagination...")
        
        exit_tagged_contacts = {}
        
        for exit_tag in config.REMOVAL_TRIGGER_TAGS:
            self.log(f"   📋 Scanning for tag: {exit_tag}")
            
            # Phase 5: Pagination variables
            page_size = 1000  # Mailchimp max per request
            offset = 0
            total_scanned = 0
            tag_contacts = []
            page = 1
            
            while True:
                self.log(f"   📄 Fetching page {page} (offset {offset}) for tag '{exit_tag}'")
                
                # Get members with pagination
                url = f"{self.mailchimp_base_url}/lists/{config.MAILCHIMP_LIST_ID}/members"
                params = {
                    'count': page_size,
                    'offset': offset,
                    'fields': 'members.email_address,members.merge_fields,members.tags,members.id,members.timestamp_signup,total_items'
                }
                
                try:
                    response = self.session.get(
                        url,
                        params=params,
                        auth=('user', config.MAILCHIMP_API_KEY)
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    members = data.get('members', [])
                    total_items = data.get('total_items', 0)
                    
                    # Log progress for large lists
                    if page == 1:
                        self.log(f"   📊 Total members in list: {total_items:,}")
                        if total_items > page_size:
                            expected_pages = math.ceil(total_items / page_size)
                            self.log(f"   📈 Expected pages: {expected_pages}")
                    
                    # Process this page
                    page_tagged = 0
                    for member in members:
                        total_scanned += 1
                        
                        # Check if member has the exit tag
                        member_tags = [tag['name'] for tag in member.get('tags', [])]
                        if exit_tag in member_tags:
                            # Extract source list information from merge fields
                            merge_fields = member.get('merge_fields', {})
                            source_list_id = merge_fields.get(ORI_LISTS_FIELD, '')
                            
                            contact_data = {
                                'email': member['email_address'],
                                'mailchimp_id': member['id'],
                                'merge_fields': merge_fields,
                                'tags': member_tags,
                                'signup_timestamp': member.get('timestamp_signup'),
                                'source_list_id': source_list_id  # 🔍 Source list tracking
                            }
                            
                            tag_contacts.append(contact_data)
                            page_tagged += 1
                            
                            # Debug logging for source tracking
                            if source_list_id:
                                self.log(f"   🎯 Contact {member['email_address']} from source list {source_list_id}")
                            else:
                                self.log(f"   ⚠️ Contact {member['email_address']} has no source list information")
                    
                    if page_tagged > 0:
                        self.log(f"   ✅ Page {page}: Found {page_tagged} contacts with tag '{exit_tag}'")
                    else:
                        self.log(f"   ➖ Page {page}: No contacts with tag '{exit_tag}' ({len(members)} scanned)")
                    
                    # Check if we've processed all members
                    if len(members) < page_size:
                        self.log(f"   🏁 Completed scanning for tag '{exit_tag}': {total_scanned:,} total scanned")
                        break
                        
                    # Prepare for next page
                    offset += page_size
                    page += 1
                    
                    # Rate limiting for large operations
                    if page > 1:
                        time.sleep(0.5)  # Respect Mailchimp rate limits
                        
                except Exception as e:
                    self.log(f"   ❌ Error on page {page} for tag '{exit_tag}': {str(e)}", "ERROR")
                    self.stats['errors'] += 1
                    break  # Exit pagination loop on error
            
            # Store results for this tag
            if tag_contacts:
                exit_tagged_contacts[exit_tag] = tag_contacts
                self.log(f"   ✅ Final count for '{exit_tag}': {len(tag_contacts)} contacts")
            else:
                self.log(f"   ➖ No contacts found with tag '{exit_tag}' (scanned {total_scanned:,} total)")
        
        total_contacts = sum(len(contacts) for contacts in exit_tagged_contacts.values())
        self.log(f"📊 Phase 5 Complete: {total_contacts:,} total exit-tagged contacts found across all tags")
        
        return exit_tagged_contacts
    
    def _get_cutoff_timestamp(self) -> str:
        """Get timestamp for filtering recent contacts"""
        cutoff = datetime.now() - timedelta(hours=config.SECONDARY_SYNC_DELAY_HOURS)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    
    def _batch_find_hubspot_contacts(self, emails: List[str], batch_size: int = 100) -> Dict[str, str]:
        """
        Phase 5: Batch lookup of HubSpot contacts by email for performance optimization.
        
        Args:
            emails: List of email addresses to look up
            batch_size: Number of emails to process per batch
            
        Returns:
            Dict mapping email -> contact_id for found contacts
        """
        logger.info(f"🔍 Phase 5: Batch lookup of {len(emails)} emails in HubSpot (batch size: {batch_size})")
        
        email_to_id = {}
        email_batches = [emails[i:i + batch_size] for i in range(0, len(emails), batch_size)]
        
        for batch_num, email_batch in enumerate(email_batches, 1):
            logger.debug(f"Processing email batch {batch_num}/{len(email_batches)} ({len(email_batch)} emails)")
            
            try:
                # Use HubSpot's batch search API for better performance
                batch_url = "https://api.hubapi.com/crm/v3/objects/contacts/batch/read"
                headers = {
                    "Authorization": f"Bearer {config.HUBSPOT_PRIVATE_TOKEN}",
                    "Content-Type": "application/json"
                }
                
                # Create batch payload with email-based inputs
                batch_payload = {
                    "properties": ["email"],
                    "inputs": [{"id": email} for email in email_batch],
                    "idProperty": "email"  # Search by email instead of ID
                }
                
                response = requests.post(batch_url, headers=headers, json=batch_payload)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    
                    for contact in results:
                        contact_id = contact.get("id")
                        contact_email = contact.get("properties", {}).get("email", "").lower()
                        
                        if contact_id and contact_email:
                            email_to_id[contact_email] = contact_id
                            logger.debug(f"Found: {contact_email} → ID {contact_id}")
                    
                    logger.debug(f"Batch {batch_num}: Found {len(results)} contacts")
                    
                elif response.status_code == 207:  # Multi-status response
                    # Handle partial success
                    data = response.json()
                    results = data.get("results", [])
                    errors = data.get("errors", [])
                    
                    for contact in results:
                        contact_id = contact.get("id")
                        contact_email = contact.get("properties", {}).get("email", "").lower()
                        
                        if contact_id and contact_email:
                            email_to_id[contact_email] = contact_id
                    
                    logger.warning(f"Batch {batch_num}: Partial success - {len(results)} found, {len(errors)} errors")
                    
                else:
                    logger.warning(f"Batch {batch_num}: API error {response.status_code}, falling back to individual lookups")
                    # Fallback to individual lookups for this batch
                    for email in email_batch:
                        contact_id = self._find_hubspot_contact_by_email(email)
                        if contact_id:
                            email_to_id[email.lower()] = contact_id
                
                # Rate limiting between batches
                if batch_num < len(email_batches):
                    time.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Error in batch {batch_num}: {str(e)}, falling back to individual lookups")
                # Fallback to individual lookups for this batch
                for email in email_batch:
                    try:
                        contact_id = self._find_hubspot_contact_by_email(email)
                        if contact_id:
                            email_to_id[email.lower()] = contact_id
                    except Exception as individual_error:
                        logger.error(f"Individual lookup failed for {email}: {str(individual_error)}")
        
        logger.info(f"📊 Batch lookup complete: {len(email_to_id)}/{len(emails)} contacts found in HubSpot")
        return email_to_id
    
    def import_to_hubspot_list(self, contacts: List[Dict], target_list_id: str, operation_id: str = None) -> int:
        """
        Move contacts to specified HubSpot list based on Mailchimp tags using v3 bulk operations.
        This preserves all existing HubSpot contact data and uses efficient batch processing.
        
        Args:
            contacts: List of contact dictionaries from Mailchimp
            target_list_id: HubSpot list ID to add contacts to  
            operation_id: Optional operation ID for atomic tracking
            
        Returns:
            int: Number of successfully added contacts to the target list
        """
        if not contacts:
            return 0
            
        logger.info(f"📋 Phase 5: Processing {len(contacts)} contacts to HubSpot list {target_list_id}")
        
        # Initialize progress tracking for large operations
        operation_start = datetime.now()
        
        try:
            # Phase 1: Pre-flight validation
            logger.debug("🔍 Phase 1: Pre-flight validation")
            validation_result = self.list_manager.validate_list_for_migration_v3(target_list_id)
            
            if not validation_result.get("valid"):
                errors = validation_result.get("errors", ["Unknown validation error"])
                logger.error(f"❌ Target list {target_list_id} validation failed: {', '.join(errors)}")
                self.stats['errors'] = self.stats.get('errors', 0) + len(contacts)
                return 0
            
            list_info = validation_result.get("list_info", {})
            list_name = list_info.get('name', f'List-{target_list_id}')
            logger.info(f"✅ Target list validated: {list_name} ({list_info.get('processingType')})")
            
            # Phase 2: Find existing HubSpot contacts in bulk (Phase 5 optimization)
            logger.debug("🔍 Phase 2: Finding existing contacts in HubSpot using batch lookup")
            
            # Extract emails for batch processing
            emails = [contact['email'].lower() for contact in contacts]
            
            # Show progress for large contact sets
            if len(emails) > 500:
                self.log(f"📊 Large contact set detected ({len(emails):,} contacts) - using Phase 5 optimizations")
            
            # Use Phase 5 batch lookup for performance
            email_to_id = self._batch_find_hubspot_contacts(emails, batch_size=100)
            
            # Process results
            contact_ids = []
            not_found_emails = []
            
            for contact in contacts:
                email = contact['email'].lower()
                contact_id = email_to_id.get(email)
                
                if contact_id:
                    contact_ids.append(contact_id)
                    logger.debug(f"Found: {email} → ID {contact_id}")
                else:
                    not_found_emails.append(email)
                    logger.debug(f"Not found in HubSpot: {email}")
            
            logger.info(f"📊 Phase 5 batch lookup complete: {len(contact_ids)} found, {len(not_found_emails)} not found")
            
            if not contact_ids:
                logger.warning("⚠️ No valid contacts found for migration")
                self.stats['errors'] = self.stats.get('errors', 0) + len(contacts)
                return 0
            
            # Record reversible action for atomic operation
            if operation_id:
                self.record_reversible_action(operation_id, "add_to_list", {
                    "list_id": target_list_id,
                    "contact_ids": contact_ids.copy()
                })
            
            # Phase 3: Bulk migration using v3 API
            logger.info(f"🚀 Phase 3: Bulk migration of {len(contact_ids)} contacts using v3 API")
            
            migration_result = self.list_manager.batch_add_contacts_v3(
                target_list_id, 
                contact_ids, 
                batch_size=100
            )
            
            if migration_result.get("success"):
                imported_count = migration_result.get("total_added", 0)
                logger.info(f"✅ Bulk migration successful: {imported_count} contacts added to list {target_list_id}")
                
                # Phase 4: Update Import List custom property for tracking
                if imported_count > 0:
                    logger.info(f"🏷️ Phase 4: Setting Import List property based on source lists for {imported_count} contacts")
                    
                    # Create mapping of source_list_id to friendly name for efficient lookup
                    source_name_cache = {}
                    
                    # Helper function to get friendly source list name
                    def get_source_list_name(source_list_id: str) -> str:
                        if not source_list_id:
                            return "Unknown"
                        
                        # Check cache first
                        if source_list_id in source_name_cache:
                            return source_name_cache[source_list_id]
                        
                        # Handle manual override markers (e.g., "784_via_720")
                        if "_via_" in source_list_id:
                            # For manual overrides, show the target campaign name
                            target_list_id = source_list_id.split("_via_")[1]
                            campaign_names = {
                                "718": "Recruitment",
                                "719": "Competition", 
                                "720": "General",
                                "751": "Directors"
                            }
                            name = campaign_names.get(target_list_id, "General")
                            source_name_cache[source_list_id] = name
                            return name
                        
                        # Regular source list ID - resolve to friendly name
                        try:
                            from .sync import fetch_hubspot_list_name
                            name = fetch_hubspot_list_name(source_list_id)
                            source_name_cache[source_list_id] = name
                            return name
                        except Exception:
                            # Fallback to basic mapping for known lists
                            known_lists = {
                                "718": "Recruitment",
                                "719": "Competition", 
                                "720": "General",
                                "751": "Directors"
                            }
                            name = known_lists.get(source_list_id, f"List-{source_list_id}")
                            source_name_cache[source_list_id] = name
                            return name
                    
                    # Update contacts with Import List property in batches to avoid rate limits
                    property_update_count = 0
                    batch_size = 50  # Conservative batch size for property updates
                    
                    # Create mapping of contact_id to source_list_id for this batch
                    contact_source_mapping = {}
                    for contact in contacts:
                        email = contact['email'].lower()
                        contact_id = email_to_id.get(email)
                        if contact_id:
                            contact_source_mapping[contact_id] = contact.get('source_list_id', '')
                    
                    for i in range(0, len(contact_ids), batch_size):
                        batch_contact_ids = contact_ids[i:i + batch_size]
                        
                        for contact_id in batch_contact_ids:
                            try:
                                # Get the source list ID for this contact
                                source_list_id = contact_source_mapping.get(contact_id, '')
                                source_list_name = get_source_list_name(source_list_id)
                                
                                # Update the Import List custom property with source list name
                                properties = {config.IMPORT_LIST_PROPERTY: source_list_name}
                                
                                if self.list_manager.update_contact_properties(contact_id, properties):
                                    property_update_count += 1
                                    logger.debug(f"✅ Set import_list='{source_list_name}' for contact {contact_id} (source: {source_list_id})")
                                else:
                                    logger.warning(f"⚠️ Failed to set import_list property for contact {contact_id}")
                                    
                            except Exception as e:
                                logger.warning(f"⚠️ Error updating import_list property for contact {contact_id}: {e}")
                        
                        # Small delay between batches to respect rate limits
                        if i + batch_size < len(contact_ids):
                            time.sleep(0.2)
                    
                    logger.info(f"✅ Import List property updated for {property_update_count}/{imported_count} contacts with source list names")
                    
                    # Log summary of source lists processed
                    if source_name_cache:
                        logger.info(f"📊 Source list name mapping: {dict(source_name_cache)}")
                
                
                # Log any failed batches for audit
                failed_batches = migration_result.get("failed_batches", [])
                if failed_batches:
                    logger.warning(f"⚠️ {len(failed_batches)} batches had failures - check logs")
                    for failure in failed_batches:
                        logger.warning(f"Batch {failure.get('batch_index')}: {failure.get('error')}")
                
                # Phase 5: Memory optimization for large operations
                if len(contacts) > 1000:
                    self._optimize_memory_usage("bulk_migration")
                    
            else:
                logger.error("❌ Bulk migration failed")
                imported_count = 0
                self.stats['errors'] = self.stats.get('errors', 0) + len(contact_ids)
            
            # Update stats
            self.stats['contacts_imported'] = self.stats.get('contacts_imported', 0) + imported_count
            self.stats['errors'] = self.stats.get('errors', 0) + len(not_found_emails)  # Count not-found as errors
            
            logger.info(f"📊 Migration summary: {imported_count}/{len(contacts)} contacts successfully added to list {target_list_id}")
            
            return imported_count
            
        except Exception as e:
            logger.error(f"❌ Error in import_to_hubspot_list: {e}")
            self.stats['errors'] = self.stats.get('errors', 0) + len(contacts)
            return 0
            
            return imported_count
            
        except Exception as e:
            self.log(f"❌ Exception during import to HubSpot list: {str(e)}", "ERROR")
            self.stats['errors'] += len(contacts)
            self.complete_atomic_operation(operation_id, 'failed')
            return 0
    
    def _prepare_hubspot_contact_data(self, mailchimp_contact: Dict, exit_tag: str) -> Dict:
        """Prepare contact data for HubSpot import"""
        merge_fields = mailchimp_contact.get('merge_fields', {})
        
        # Map Mailchimp fields to HubSpot properties
        hubspot_data = {
            'email': mailchimp_contact['email'],
            'firstname': merge_fields.get('FNAME', ''),
            'lastname': merge_fields.get('LNAME', ''),
            'company': merge_fields.get('COMPANY', ''),
            'phone': merge_fields.get('PHONE', ''),
            'address': merge_fields.get('ADDRESS', ''),
            'city': merge_fields.get('CITY', ''),
            'state': merge_fields.get('STATE', ''),
            'zip': merge_fields.get('POSTCODE', ''),
            'country': merge_fields.get('COUNTRY', ''),
            # Custom properties
            'mailchimp_exit_tag': exit_tag,
            'mailchimp_sync_date': datetime.now().isoformat(),
            'journey_completion_source': 'mailchimp_secondary_sync'
        }
        
        # Remove empty values
        return {k: v for k, v in hubspot_data.items() if v}
    
    def _create_or_update_hubspot_contact(self, contact_data: Dict, email: str) -> Optional[str]:
        """Create or update contact in HubSpot, returns contact ID"""
        url = f"{self.hubspot_base_url}/crm/v3/objects/contacts"
        
        # Prepare properties for HubSpot API
        properties = {k: str(v) for k, v in contact_data.items()}
        
        payload = {
            "properties": properties
        }
        
        headers = {
            'Authorization': f'Bearer {config.HUBSPOT_PRIVATE_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        try:
            # Try to create the contact
            response = self.session.post(url, json=payload, headers=headers)
            
            if response.status_code == 201:
                # Successfully created
                return response.json()['id']
            elif response.status_code == 409:
                # Contact exists, update it
                return self._update_existing_hubspot_contact(email, properties)
            else:
                self.log(f"   ❌ Unexpected response creating contact: {response.status_code}", "ERROR")
                return None
                
        except Exception as e:
            self.log(f"   ❌ Error creating/updating contact: {str(e)}", "ERROR")
            return None
    
    def _update_existing_hubspot_contact(self, email: str, properties: Dict) -> Optional[str]:
        """Update existing HubSpot contact by email"""
        # First, get the contact ID by email
        search_url = f"{self.hubspot_base_url}/crm/v3/objects/contacts/search"
        
        search_payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email
                }]
            }],
            "properties": ["id"]
        }
        
        headers = {
            'Authorization': f'Bearer {config.HUBSPOT_PRIVATE_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        try:
            search_response = self.session.post(search_url, json=search_payload, headers=headers)
            search_response.raise_for_status()
            
            results = search_response.json().get('results', [])
            if not results:
                self.log(f"   ⚠️ Contact not found for update: {email}", "WARN")
                return None
            
            contact_id = results[0]['id']
            
            # Now update the contact
            update_url = f"{self.hubspot_base_url}/crm/v3/objects/contacts/{contact_id}"
            update_payload = {"properties": properties}
            
            update_response = self.session.patch(update_url, json=update_payload, headers=headers)
            update_response.raise_for_status()
            
            return contact_id
            
        except Exception as e:
            self.log(f"   ❌ Error updating existing contact: {str(e)}", "ERROR")
            return None
    
    def _add_contact_to_hubspot_list(self, contact_id: str, list_id: str) -> bool:
        """Add a contact to a HubSpot list using the list manager"""
        try:
            success = self.list_manager.add_contact_to_list(contact_id, list_id)
            if success:
                self.log(f"   ✅ Successfully added contact {contact_id} to HubSpot list {list_id}")
            else:
                self.log(f"   ❌ Failed to add contact {contact_id} to HubSpot list {list_id}", "ERROR")
            return success
        except Exception as e:
            self.log(f"   ❌ Exception adding contact {contact_id} to HubSpot list {list_id}: {str(e)}", "ERROR")
            return False
    
    def remove_from_source_lists(self, all_exit_contacts: dict, operation_id: str = None) -> int:
        """
        Remove contacts from their original source HubSpot lists to prevent re-marketing using source-aware removal.
        This replaces the old broadcast removal approach with precise source list targeting.
        
        Args:
            all_exit_contacts: Dict mapping exit tags to contact data (with source_list_id tracking)
            operation_id: Optional operation ID for atomic tracking
            
        Returns:
            int: Total number of contacts removed
        """
        logger.info(f"🎯 Source-aware anti-remarketing: Removing contacts from their original source lists")
        
        total_removed = 0
        protected_count = 0  # Track manual override protections
        source_list_groups = {}  # Group contacts by their source list ID
        
        # Phase 1: Group contacts by their original source list ID
        for exit_tag, tag_data in all_exit_contacts.items():
            contacts = tag_data.get("contacts", [])
            logger.info(f"🏷️ Processing {len(contacts)} contacts with exit tag '{exit_tag}'")
            
            for contact in contacts:
                source_list_id = contact.get('source_list_id', '')
                
                # 🛡️ PROTECTION: Skip removal for manual inclusion overrides
                if source_list_id and "_via_" in source_list_id:
                    original_override_list = source_list_id.split("_via_")[0]
                    from .config import MANUAL_INCLUSION_OVERRIDE_LISTS
                    if original_override_list in MANUAL_INCLUSION_OVERRIDE_LISTS:
                        logger.info(f"🛡️ PROTECTING manual override contact {contact['email']} from source removal")
                        logger.debug(f"🛡️ Contact originated from manual inclusion list {original_override_list}")
                        logger.debug(f"🛡️ Source marker: {source_list_id}")
                        protected_count += 1
                        continue
                
                if not source_list_id:
                    logger.warning(f"⚠️ Contact {contact['email']} has no source list information - skipping removal")
                    continue
                
                # Initialize source list group if needed
                if source_list_id not in source_list_groups:
                    source_list_groups[source_list_id] = {
                        'contacts': [],
                        'emails': set()  # To avoid duplicates
                    }
                
                # Add contact if not already present (deduplicate by email)
                email = contact['email'].lower()
                if email not in source_list_groups[source_list_id]['emails']:
                    source_list_groups[source_list_id]['contacts'].append(contact)
                    source_list_groups[source_list_id]['emails'].add(email)
                    
                    logger.debug(f"   🎯 Queued {email} for removal from source list {source_list_id}")
        
        logger.info(f"📊 Source grouping complete: {len(source_list_groups)} source lists identified")
        
        # Phase 2: Process each source list group for source-aware removal
        for source_list_id, group_data in source_list_groups.items():
            contacts = group_data['contacts']
            logger.info(f"🗑️ Processing source list {source_list_id}: {len(contacts)} contacts to remove")
            
            # Check if this source list has exclusion rules configured
            if source_list_id not in LIST_EXCLUSION_RULES:
                logger.info(f"ℹ️ Source list {source_list_id} has no exclusion rules - skipping removal")
                continue
            
            try:
                # Phase 2a: Find HubSpot contact IDs for this source list's contacts
                emails = [contact['email'].lower() for contact in contacts]
                email_to_id = self._batch_find_hubspot_contacts(emails, batch_size=100)
                
                contact_ids = []
                not_found_emails = []
                
                for contact in contacts:
                    email = contact['email'].lower()
                    contact_id = email_to_id.get(email)
                    
                    if contact_id:
                        contact_ids.append(contact_id)
                    else:
                        not_found_emails.append(email)
                
                logger.info(f"   📊 Batch lookup for source list {source_list_id}: {len(contact_ids)} found, {len(not_found_emails)} not found")
                
                if not contact_ids:
                    logger.warning(f"   ⚠️ No valid contacts found in HubSpot for source list {source_list_id}")
                    continue
                
                # Phase 2b: Pre-flight validation for source list
                validation_result = self.list_manager.validate_list_for_migration_v3(source_list_id)
                
                if not validation_result.get("valid"):
                    errors = validation_result.get("errors", ["Unknown validation error"])
                    logger.error(f"   ❌ Source list {source_list_id} validation failed: {', '.join(errors)}")
                    continue
                
                # Phase 2c: Record reversible action for atomic operation
                if operation_id:
                    self.record_reversible_action(operation_id, "remove_from_source_list", {
                        "source_list_id": source_list_id,
                        "contact_ids": contact_ids.copy(),
                        "emails": emails.copy(),
                        "exclusion_rules": LIST_EXCLUSION_RULES.get(source_list_id, [])
                    })
                
                # Phase 2d: Execute source-aware bulk removal
                logger.info(f"   🚀 Executing source-aware removal from list {source_list_id}")
                
                removal_result = self.list_manager.batch_remove_contacts_v3(
                    source_list_id, 
                    contact_ids, 
                    batch_size=100
                )
                
                if removal_result.get("success"):
                    removed_count = removal_result.get("total_removed", 0)
                    total_removed += removed_count
                    logger.info(f"   ✅ Source-aware removal successful: {removed_count} contacts removed from source list {source_list_id}")
                    
                    # Log any failed batches for audit
                    failed_batches = removal_result.get("failed_batches", [])
                    if failed_batches:
                        logger.warning(f"   ⚠️ {len(failed_batches)} removal batches had failures for source list {source_list_id}")
                else:
                    logger.error(f"   ❌ Source-aware removal failed for list {source_list_id}")
                    self.stats['errors'] = self.stats.get('errors', 0) + len(contact_ids)
                
            except Exception as e:
                logger.error(f"   ❌ Error processing source list {source_list_id}: {str(e)}")
                self.stats['errors'] = self.stats.get('errors', 0) + len(contacts)
        
        # Phase 3: Update statistics and summary
        self.stats['contacts_removed_from_source'] = self.stats.get('contacts_removed_from_source', 0) + total_removed
        
        logger.info(f"🎯 Source-aware anti-remarketing complete: {total_removed} total removals across {len(source_list_groups)} source lists")
        
        # Manual override protection summary
        if protected_count > 0:
            logger.info(f"🛡️ PROTECTION SUMMARY: Protected {protected_count} manual inclusion override contacts from source removal")
        
        # Phase 4: Validation summary
        if total_removed > 0:
            logger.info(f"✅ Source-aware removal successfully processed contacts from their original lists")
        else:
            logger.warning("⚠️ No contacts were removed - check source list tracking and exclusion rules")
            
        return total_removed
    
    def _find_hubspot_contact_by_email(self, email: str) -> Optional[str]:
        """Find HubSpot contact ID by email address"""
        search_url = f"{self.hubspot_base_url}/crm/v3/objects/contacts/search"
        
        search_payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email
                }]
            }],
            "properties": ["id"]
        }
        
        headers = {
            'Authorization': f'Bearer {config.HUBSPOT_PRIVATE_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = self.session.post(search_url, json=search_payload, headers=headers)
            response.raise_for_status()
            
            results = response.json().get('results', [])
            return results[0]['id'] if results else None
            
        except Exception as e:
            self.log(f"Error finding contact by email: {str(e)}", "ERROR")
            return None
    
    def archive_mailchimp_contacts(self, all_exit_contacts: dict, operation_id: str = None) -> int:
        """
        Archive processed contacts from Mailchimp.
        Supports atomic operations with rollback capability (note: archival is typically irreversible).
        
        Args:
            all_exit_contacts: Dict mapping exit tags to contact data
            operation_id: Optional operation ID for atomic tracking
            
        Returns:
            int: Total number of contacts archived
        """
        if not ENABLE_MAILCHIMP_ARCHIVAL:
            logger.info("⏭️ Mailchimp archival disabled, skipping")
            return 0
        
        logger.info("📦 Archiving processed contacts from Mailchimp")
        
        archived_count = 0
        
        # Process each exit tag group
        for exit_tag, tag_data in all_exit_contacts.items():
            contacts = tag_data["contacts"]
            logger.info(f"📦 Archiving {len(contacts)} contacts with exit tag '{exit_tag}'")
            
            for contact in contacts:
                try:
                    email = contact['email']
                    mailchimp_id = contact.get('mailchimp_id')
                    
                    if not mailchimp_id:
                        logger.warning(f"⚠️ No Mailchimp ID for {email}, skipping archival")
                        continue
                    
                    # Record reversible action (though archival is typically permanent)
                    if operation_id:
                        self.record_reversible_action(operation_id, "archive_contact", {
                            "email": email,
                            "mailchimp_id": mailchimp_id,
                            "exit_tag": exit_tag,
                            "note": "Mailchimp archival is typically irreversible"
                        })
                    
                    # Archive the member using DELETE (permanent removal)
                    # Note: In Mailchimp, "archiving" means permanent deletion
                    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
                    url = f"{self.mailchimp_base_url}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                    
                    # DELETE request to permanently remove from audience
                    response = self.session.delete(
                        url,
                        auth=('user', MAILCHIMP_API_KEY)
                    )
                    
                    if response.status_code in [200, 204, 404]:
                        # 404 is acceptable - means already archived/removed
                        archived_count += 1
                        if response.status_code == 404:
                            logger.debug(f"✅ Contact {email} was already archived/removed from Mailchimp")
                        else:
                            logger.debug(f"✅ Archived: {email}")
                    else:
                        response.raise_for_status()
                    
                    # ────────────────
                    # Clear ORI_LISTS only on 0 errors (using subscriber hash approach)
                    # ────────────────
                    if self.stats.get('errors', 0) == 0:
                        # Use subscriber hash for member identification
                        member_url = f"{self.mailchimp_base_url}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                        patch_payload = {"merge_fields": {ORI_LISTS_FIELD: ""}}
                        try:
                            patch_resp = self.session.patch(
                                member_url,
                                auth=('user', MAILCHIMP_API_KEY),
                                json=patch_payload
                            )
                            if patch_resp.status_code not in [404]:
                                # 404 is expected after deletion, but try anyway for completeness
                                patch_resp.raise_for_status()
                                logger.info(f"🗑️ Cleared ORI_LISTS for contact {email}")
                        except Exception as e:
                            # Expected to fail after deletion - this is normal
                            logger.debug(f"Expected: Failed to clear ORI_LISTS for archived contact {email}: {e}")
                    
                except Exception as e:
                    logger.error(f"❌ Error archiving {contact.get('email', 'unknown')}: {str(e)}")
                    self.stats['errors'] = self.stats.get('errors', 0) + 1
        
        self.stats['contacts_archived'] = self.stats.get('contacts_archived', 0) + archived_count
        logger.info(f"📊 Successfully archived {archived_count} contacts from Mailchimp")
        
        return archived_count
    
    def save_raw_data(self, exit_tagged_contacts: Dict[str, List[Dict]]):
        """Save raw data for audit trail"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"secondary_sync_raw_data_{timestamp}.json"
        filepath = os.path.join(self.raw_data_dir, filename)
        
        data = {
            'timestamp': timestamp,
            'config': {
                'secondary_sync_mode': config.SECONDARY_SYNC_MODE,
                'enable_mailchimp_archival': config.ENABLE_MAILCHIMP_ARCHIVAL,
                'secondary_sync_mappings': config.SECONDARY_SYNC_MAPPINGS,
                'list_exclusion_rules': config.LIST_EXCLUSION_RULES
            },
            'exit_tagged_contacts': exit_tagged_contacts,
            'stats': self.stats
        }
        
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            self.log(f"💾 Raw data saved: {filepath}")
        except Exception as e:
            self.log(f"❌ Error saving raw data: {str(e)}", "ERROR")
    
    def generate_summary_report(self) -> str:
        """Generate summary report of secondary sync operation"""
        duration = datetime.now() - self.stats['start_time']
        
        report = f"""
🔄 SECONDARY SYNC SUMMARY REPORT
{'='*50}
⏱️  Duration: {duration}
📊 Total Contacts Processed: {self.stats['contacts_processed']}
📥 Contacts Imported to HubSpot: {self.stats['contacts_imported']}
🗑️  Contacts Removed from Source Lists: {self.stats['contacts_removed_from_source']}
📦 Contacts Archived from Mailchimp: {self.stats['contacts_archived']}
❌ Errors Encountered: {self.stats['errors']}

🔍 PHASE 3 VERIFICATION RESULTS:
📋 Verifications Attempted: {self.stats.get('verifications_attempted', 0)}
✅ Verifications Successful: {self.stats.get('verifications_successful', 0)}
❌ Verifications Failed: {self.stats.get('verifications_failed', 0)}

📋 MAPPINGS PROCESSED:
"""
        
        for exit_tag, target_list in config.SECONDARY_SYNC_MAPPINGS.items():
            report += f"   • {exit_tag} → HubSpot List {target_list}\n"
        
        report += f"\n✅ Secondary sync completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return report
    
    def run_migration_verification(self):
        """
        Run Phase 3 delayed verification for all pending migrations
        Uses intelligent retry logic to handle HubSpot's list membership propagation delays
        """
        if not hasattr(self, 'pending_verifications') or not self.pending_verifications:
            self.log("📊 No migrations to verify")
            return {
                "verification_success": True,
                "total_verifications": 0,
                "successful_verifications": 0,
                "failed_verifications": 0
            }
        
        self.log(f"🔍 Phase 3: Verifying {len(self.pending_verifications)} migration operations")
        
        verification_results = []
        
        for migration_key, migration_data in self.pending_verifications.items():
            self.log(f"🔍 Verifying migration: {migration_key}")
            
            verification_type = migration_data.get('verification_type', 'migration')
            
            if verification_type == 'removal':
                # Verify anti-remarketing removals
                result = self._verify_removal_operation(migration_data)
            else:
                # Verify standard migration (target list addition)
                result = self._verify_migration_operation(migration_data)
            
            verification_results.append(result)
            
            # Save audit log for compliance
            if result.get('audit_log'):
                audit_file = self.list_manager.save_audit_log(
                    result['audit_log'], 
                    f"secondary_sync_{verification_type}"
                )
                
                # Generate detailed audit report
                audit_report = self.list_manager.create_migration_audit_report(result['audit_log'])
                self.log(f"📋 Audit report for {migration_key}:")
                self.log(audit_report)
        
        # Summary of verification results
        successful_verifications = sum(1 for r in verification_results if r.get('verified'))
        total_verifications = len(verification_results)
        
        self.log(f"📊 Phase 3 verification complete: {successful_verifications}/{total_verifications} operations verified")
        
        # Update stats with verification results
        self.stats['verifications_attempted'] = total_verifications
        self.stats['verifications_successful'] = successful_verifications
        self.stats['verifications_failed'] = total_verifications - successful_verifications
        
        # Return verification result summary
        return {
            "verification_success": successful_verifications == total_verifications,
            "total_verifications": total_verifications,
            "successful_verifications": successful_verifications,
            "failed_verifications": total_verifications - successful_verifications
        }
    
    def _verify_migration_operation(self, migration_data: Dict) -> Dict:
        """Verify a standard migration operation (contact addition to target list)"""
        target_list_id = migration_data['target_list_id']
        contact_ids = migration_data['contact_ids']
        exit_tag = migration_data['exit_tag']
        
        self.log(f"   🎯 Verifying addition of {len(contact_ids)} contacts to list {target_list_id}")
        
        # Use custom verification for target list addition
        verification_result = self._verify_contact_presence(
            list_id=target_list_id,
            contact_ids=contact_ids,
            operation_type="migration"
        )
        
        if verification_result.get('verified'):
            self.log(f"   ✅ Migration verification successful: {exit_tag} → list {target_list_id}")
        else:
            self.log(f"   ❌ Migration verification failed: {exit_tag} → list {target_list_id}", "ERROR")
        
        return verification_result
    
    def _verify_contact_presence(self, list_id: str, contact_ids: List[str], operation_type: str) -> Dict:
        """
        Verify that contacts ARE present in a list (for migration verification)
        Uses delayed checking with retry logic
        """
        import time
        
        verification_id = f"{operation_type}_{list_id}_{int(time.time())}"
        
        self.log(f"       🔍 Verifying contact presence in list {list_id}")
        
        # Wait for propagation (5 minutes for migrations)
        self.log(f"       ⏳ Waiting 5 minutes for HubSpot propagation...")
        time.sleep(300)  # 5 minutes
        
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # Get current list memberships
                current_members = self.list_manager.get_list_memberships_v3(list_id)
                
                # Check how many expected contacts are present
                contacts_found = [cid for cid in contact_ids if cid in current_members]
                contacts_missing = len(contact_ids) - len(contacts_found)
                
                if contacts_missing == 0:
                    # All contacts successfully added
                    self.log(f"       ✅ All {len(contact_ids)} contacts found in list {list_id}")
                    return {
                        "verified": True,
                        "list_id": list_id,
                        "contacts_found": len(contacts_found),
                        "contacts_missing": 0,
                        "verification_id": verification_id,
                        "attempts": attempt
                    }
                else:
                    self.log(f"       ⚠️ {contacts_missing} contacts missing from list {list_id} (attempt {attempt}/{max_retries})")
                    
                    if attempt < max_retries:
                        wait_time = 60 * attempt  # 1, 2, 3 minutes
                        self.log(f"       ⏳ Waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
            
            except Exception as e:
                self.log(f"       ❌ Error checking list {list_id}: {str(e)}", "ERROR")
        
        # Failed verification
        self.log(f"       ❌ Migration verification failed for list {list_id}")
        return {
            "verified": False,
            "list_id": list_id,
            "contacts_found": len(contacts_found) if 'contacts_found' in locals() else 0,
            "contacts_missing": contacts_missing if 'contacts_missing' in locals() else len(contact_ids),
            "verification_id": verification_id,
            "attempts": max_retries
        }
    
    def _verify_removal_operation(self, removal_data: Dict) -> Dict:
        """Verify an anti-remarketing removal operation"""
        source_list_ids = removal_data['source_list_ids']
        contact_ids = removal_data['contact_ids']
        exit_tag = removal_data['exit_tag']
        
        self.log(f"   🗑️ Verifying removal of {len(contact_ids)} contacts from {len(source_list_ids)} source lists")
        
        # For removals, we check each source list to confirm contacts are no longer there
        all_verifications = []
        
        for source_list_id in source_list_ids:
            self.log(f"     🔍 Checking source list {source_list_id}")
            
            # For removal verification, we want to confirm contacts are NOT in source
            # So we create a custom verification that checks for absence
            verification_result = self._verify_contact_absence(
                list_id=source_list_id,
                contact_ids=contact_ids,
                operation_type="removal"
            )
            
            all_verifications.append(verification_result)
        
        # Aggregate results for final report
        total_operations = len(all_verifications)
        successful_operations = sum(1 for v in all_verifications if v['verification_success'])
        failed_operations = total_operations - successful_operations
        
        self.log(f"📊 Removal verification complete: {successful_operations}/{total_operations} operations verified")
        
        if failed_operations > 0:
            self.log(f"⚠️ {failed_operations} removal operations failed verification")
            notifications.notify_warning("Some removal operations failed verification",
                         {"failed_operations": failed_operations,
                          "total_operations": total_operations,
                          "success_rate": f"{(successful_operations/total_operations)*100:.1f}%"})
        
        return {
            "verification_success": failed_operations == 0,
            "total_operations": total_operations,
            "successful_operations": successful_operations,
            "failed_operations": failed_operations,
            "details": all_verifications
        }

    def _verify_contact_absence(self, list_id: str, contact_ids: list, operation_type: str) -> dict:
        """
        Verify that contacts are no longer present in a specific list.
        Used for confirming removal operations.
        
        Args:
            list_id: HubSpot list ID to check
            contact_ids: List of contact IDs that should be absent
            operation_type: Type of operation being verified (for logging)
            
        Returns:
            dict: Verification results with success status and details
        """
        self.log(f"🔍 Verifying {operation_type}: {len(contact_ids)} contacts should be absent from list {list_id}")
        
        try:
            # Get current list membership
            current_members = self.list_manager.get_list_membership(list_id)
            current_member_ids = {str(member_id) for member_id in current_members}
            
            # Check which contacts are still present (should be none)
            still_present = []
            for contact_id in contact_ids:
                if str(contact_id) in current_member_ids:
                    still_present.append(contact_id)
                    self.log(f"❌ Contact {contact_id} still present in list {list_id} after {operation_type}")
            
            verification_success = len(still_present) == 0
            
            if verification_success:
                self.log(f"✅ All {len(contact_ids)} contacts successfully absent from list {list_id}")
            else:
                self.log(f"❌ {len(still_present)} contacts still present in list {list_id} after {operation_type}")
            
            return {
                "verification_success": verification_success,
                "list_id": list_id,
                "operation_type": operation_type,
                "expected_absent": len(contact_ids),
                "still_present": len(still_present),
                "still_present_ids": still_present
            }
            
        except Exception as e:
            self.log(f"❌ Error verifying {operation_type} for list {list_id}: {e}")
            return {
                "verification_success": False,
                "list_id": list_id,
                "operation_type": operation_type,
                "error": str(e),
                "expected_absent": len(contact_ids),
                "still_present": "unknown"
            }
    
    # =============================================================================
    # 📊 PHASE 5: PROGRESS TRACKING AND PERFORMANCE MONITORING  
    # =============================================================================
    
    def _calculate_progress_eta(self, current: int, total: int, start_time: datetime) -> str:
        """
        Phase 5: Calculate estimated time to completion for large operations.
        
        Args:
            current: Current number of processed items
            total: Total number of items to process
            start_time: When the operation started
            
        Returns:
            str: Formatted ETA string
        """
        if current == 0 or total == 0:
            return "Calculating..."
        
        elapsed = datetime.now() - start_time
        rate = current / elapsed.total_seconds()  # items per second
        remaining = total - current
        
        if rate > 0:
            eta_seconds = remaining / rate
            eta_minutes = eta_seconds / 60
            
            if eta_minutes < 1:
                return f"~{int(eta_seconds)}s remaining"
            elif eta_minutes < 60:
                return f"~{int(eta_minutes)}m remaining"
            else:
                eta_hours = eta_minutes / 60
                return f"~{int(eta_hours)}h {int(eta_minutes % 60)}m remaining"
        else:
            return "Calculating..."
    
    def _log_progress(self, operation: str, current: int, total: int, start_time: datetime, 
                      interval: int = 100):
        """
        Phase 5: Log progress for long-running operations.
        
        Args:
            operation: Name of the operation
            current: Current progress count
            total: Total items to process
            start_time: When the operation started
            interval: Log every N items (default 100)
        """
        if current % interval == 0 or current == total:
            percentage = (current / total * 100) if total > 0 else 0
            eta = self._calculate_progress_eta(current, total, start_time)
            
            self.log(f"📊 {operation}: {current:,}/{total:,} ({percentage:.1f}%) - {eta}")
    
    def _optimize_memory_usage(self, operation_name: str):
        """
        Phase 5: Optimize memory usage for large dataset operations.
        
        Args:
            operation_name: Name of the operation for logging
        """
        import gc
        
        # Force garbage collection
        collected = gc.collect()
        if collected > 0:
            self.log(f"🧹 {operation_name}: Cleaned up {collected} objects from memory")
        
        # Log memory optimization
        self.log(f"💾 {operation_name}: Memory optimization completed")

    # =============================================================================
    # 🛡️ PHASE 4: ATOMIC ROLLBACK INFRASTRUCTURE  
    # =============================================================================

    def start_atomic_operation(self, operation_name: str, operation_data: dict = None) -> str:
        """
        Start an atomic operation that can be rolled back if it fails.
        
        Args:
            operation_name: Human-readable name for the operation
            operation_data: Additional data about the operation
            
        Returns:
            str: Operation ID for tracking
        """
        operation_id = f"{operation_name}_{int(time.time() * 1000)}"
        
        operation_record = {
            "operation_id": operation_id,
            "operation_name": operation_name,
            "start_time": datetime.now(),
            "status": "in_progress",
            "reversible_actions": [],
            "operation_data": operation_data or {}
        }
        
        self.operation_stack.append(operation_record)
        self.rollback_journal.append(operation_record)
        
        self.log(f"🔒 Started atomic operation: {operation_name} (ID: {operation_id})")
        return operation_id

    def record_reversible_action(self, operation_id: str, action_type: str, action_data: dict):
        """
        Record an action that can be reversed during rollback.
        
        Args:
            operation_id: ID of the atomic operation
            action_type: Type of action (e.g., 'add_to_list', 'remove_from_list', 'archive_contact')
            action_data: Data needed to reverse the action
        """
        for operation in reversed(self.rollback_journal):
            if operation["operation_id"] == operation_id:
                action_record = {
                    "action_type": action_type,
                    "action_data": action_data,
                    "timestamp": datetime.now()
                }
                
                operation["reversible_actions"].append(action_record)
                self.log(f"📝 Recorded reversible action: {action_type} for operation {operation_id}")
                return
        
        self.log(f"❌ Cannot record action for unknown operation: {operation_id}", "ERROR")

    def complete_atomic_operation(self, operation_id: str, success: bool = True):
        """
        Complete an atomic operation and remove it from the rollback stack.
        
        Args:
            operation_id: ID of the operation to complete
            success: Whether the operation completed successfully
        """
        for operation in reversed(self.rollback_journal):
            if operation["operation_id"] == operation_id:
                operation["status"] = "completed" if success else "failed"
                operation["end_time"] = datetime.now()
                
                # Remove from active operation stack
                self.operation_stack = [op for op in self.operation_stack if op["operation_id"] != operation_id]
                
                status_msg = "✅ completed successfully" if success else "❌ completed with errors"
                self.log(f"🔓 Operation {operation['operation_name']} {status_msg} (ID: {operation_id})")
                
                # Track rollback metrics
                if success:
                    self.stats["operations_completed"] = self.stats.get("operations_completed", 0) + 1
                else:
                    self.stats["operations_failed"] = self.stats.get("operations_failed", 0) + 1

    def rollback_operation(self, operation_id: str) -> bool:
        """
        Rollback a specific atomic operation by reversing all its actions.
        
        Args:
            operation_id: ID of the operation to rollback
            
        Returns:
            bool: True if rollback successful, False otherwise
        """
        for operation in reversed(self.rollback_journal):
            if operation["operation_id"] == operation_id:
                operation_name = operation["operation_name"]
                reversible_actions = operation["reversible_actions"]
                
                self.log(f"🔄 Starting rollback for operation: {operation_name} (ID: {operation_id})")
                self.log(f"📝 Reversing {len(reversible_actions)} actions...")
                
                rollback_success = True
                reversed_count = 0
                
                # Reverse actions in reverse order (LIFO)
                for action in reversed(reversible_actions):
                    try:
                        if self._reverse_action(action):
                            reversed_count += 1
                        else:
                            rollback_success = False
                            self.log(f"❌ Failed to reverse action: {action['action_type']}")
                    except Exception as e:
                        self.log(f"❌ Error during action reversal: {e}")
                        rollback_success = False
                
                # Update operation status
                operation["status"] = "rolled_back" if rollback_success else "rollback_failed"
                operation["rollback_time"] = datetime.now()
                operation["actions_reversed"] = reversed_count
                
                # Remove from active stack
                self.operation_stack = [op for op in self.operation_stack if op["operation_id"] != operation_id]
                
                # Track rollback metrics
                if rollback_success:
                    self.stats["operations_rolled_back"] = self.stats.get("operations_rolled_back", 0) + 1
                    self.log(f"✅ Successfully rolled back operation: {operation_name}")
                else:
                    self.stats["rollback_failures"] = self.stats.get("rollback_failures", 0) + 1
                    self.log(f"❌ Rollback failed for operation: {operation_name}")
                
                return rollback_success

        self.log(f"❌ Cannot rollback unknown operation: {operation_id}")
        return False

    def rollback_all_operations(self) -> bool:
        """
        Rollback all operations currently on the stack.
        
        Returns:
            bool: True if all rollbacks successful, False otherwise
        """
        if not self.operation_stack:
            self.log("🔄 No operations to rollback")
            return True
        
        self.log(f"🔄 Rolling back {len(self.operation_stack)} operations...")
        
        rollback_success = True
        operations_to_rollback = list(self.operation_stack)  # Copy to avoid modification during iteration
        
        for operation in reversed(operations_to_rollback):  # Rollback in reverse order
            if not self.rollback_operation(operation["operation_id"]):
                rollback_success = False
        
        if rollback_success:
            self.log("✅ All operations successfully rolled back")
        else:
            self.log("❌ Some rollback operations failed")
            
        return rollback_success

    def _reverse_action(self, action: dict) -> bool:
        """
        Reverse a specific action based on its type.
        
        Args:
            action: Action record containing type and data
            
        Returns:
            bool: True if action reversed successfully, False otherwise
        """
        action_type = action["action_type"]
        action_data = action["action_data"]
        
        try:
            if action_type == "add_to_list":
                # Reverse: remove contacts from the list they were added to
                list_id = action_data["list_id"]
                contact_ids = action_data["contact_ids"]
                self.log(f"🔄 Reversing add_to_list: removing {len(contact_ids)} contacts from list {list_id}")
                return self.list_manager.remove_contacts_from_list(list_id, contact_ids)
                
            elif action_type == "remove_from_list":
                # Reverse: re-add contacts to the list they were removed from
                list_id = action_data["list_id"]
                contact_ids = action_data["contact_ids"]
                self.log(f"🔄 Reversing remove_from_list: re-adding {len(contact_ids)} contacts to list {list_id}")
                return self.list_manager.add_contacts_to_list(list_id, contact_ids)
                
            elif action_type == "archive_contact":
                # Note: Mailchimp archival is typically irreversible
                # This would require storing the original contact data and re-creating
                self.log(f"⚠️ Cannot reverse archive_contact action - Mailchimp archival is permanent")
                email = action_data.get("email", "unknown")
                self.log(f"⚠️ Contact {email} remains archived in Mailchimp")
                return False
                
            else:
                self.log(f"❌ Unknown action type for reversal: {action_type}")
                return False
                
        except Exception as e:
            self.log(f"❌ Error reversing {action_type} action: {e}")
            return False

    def get_rollback_status(self) -> dict:
        """
        Get current rollback system status and metrics.
        
        Returns:
            dict: Status information including active operations and metrics
        """
        return {
            "active_operations": len(self.operation_stack),
            "total_operations_tracked": len(self.rollback_journal),
            "rollback_metrics": {
                "operations_completed": self.stats.get("operations_completed", 0),
                "operations_failed": self.stats.get("operations_failed", 0),
                "operations_rolled_back": self.stats.get("operations_rolled_back", 0),
                "rollback_failures": self.stats.get("rollback_failures", 0)
            },
            "current_operation_stack": [
                {
                    "operation_id": op["operation_id"],
                    "operation_name": op["operation_name"],
                    "start_time": op["start_time"],
                    "actions_count": len(op["reversible_actions"])
                }
                for op in self.operation_stack
            ]
        }

    # =============================================================================
    # 🎯 MAIN WORKFLOW ORCHESTRATION
    # =============================================================================

    def main(self):
        """
        Main workflow orchestration method for secondary sync.
        Coordinates all phases with comprehensive error handling and rollback support.
        """
        self.log("🔄 Starting Secondary Sync (Mailchimp → HubSpot)")
        self.log("="*60)
        
        # Initialize metrics
        self.stats.update({
            "sync_start_time": datetime.now(),
            "total_contacts_processed": 0,
            "total_contacts_imported": 0,
            "total_contacts_removed": 0,
            "total_contacts_archived": 0,
            "errors_encountered": 0,
            "operations_completed": 0,
            "operations_failed": 0,
            "operations_rolled_back": 0,
            "rollback_failures": 0
        })
        
        try:
            # Send initial notification
            try:
                notifications.notify_info("Secondary sync started",
                           {"mappings": config.SECONDARY_SYNC_MAPPINGS,
                            "mode": config.SECONDARY_SYNC_MODE,
                            "test_limit": config.SECONDARY_TEST_CONTACT_LIMIT,
                            "archival_enabled": config.ENABLE_MAILCHIMP_ARCHIVAL})
            except Exception as e:
                self.log(f"Failed to send start notification: {e}", "WARN")
            
            # Phase 1: Get exit-tagged contacts from Mailchimp (Phase 5 enhanced)
            self.log("📥 Phase 1: Retrieving exit-tagged contacts from Mailchimp with pagination")
            self.log("-" * 40)
            
            # Get all exit-tagged contacts using Phase 5 pagination
            phase1_start = datetime.now()
            all_exit_tagged_contacts = self.get_exit_tagged_contacts()
            
            # Restructure data for processing
            all_exit_contacts = {}
            total_contacts_found = 0
            
            for exit_tag, destination_list in config.SECONDARY_SYNC_MAPPINGS.items():
                contacts = all_exit_tagged_contacts.get(exit_tag, [])
                if contacts:
                    # Apply test limits for secondary sync
                    if config.SECONDARY_SYNC_MODE == "TEST_RUN" and config.SECONDARY_TEST_CONTACT_LIMIT > 0:
                        original_count = len(contacts)
                        contacts = contacts[:config.SECONDARY_TEST_CONTACT_LIMIT]
                        if len(contacts) < original_count:
                            self.log(f"🧪 Test mode: Limited '{exit_tag}' from {original_count} to {len(contacts)} contacts")
                    
                    all_exit_contacts[exit_tag] = {
                        "contacts": contacts,
                        "destination_list": destination_list
                    }
                    total_contacts_found += len(contacts)
                    self.log(f"✅ Found {len(contacts)} contacts with exit tag '{exit_tag}'")
                else:
                    self.log(f"📭 No contacts found with exit tag '{exit_tag}'")
            
            if not all_exit_contacts:
                self.log("📭 No exit-tagged contacts found - secondary sync complete (no notification)")
                # Skip final notification for zero-contacts scenario to reduce noise
                return
            
            self.log(f"📊 Total contacts found: {total_contacts_found}")
            self.stats["total_contacts_processed"] = total_contacts_found
            
            # Phase 2: Import contacts to HubSpot destination lists (with atomic operations)
            self.log("\n📤 Phase 2: Importing contacts to HubSpot destination lists")
            self.log("-" * 50)
            
            import_success = True
            total_imported = 0
            
            for exit_tag, tag_data in all_exit_contacts.items():
                contacts = tag_data["contacts"]
                destination_list = tag_data["destination_list"]
                
                self.log(f"📤 Importing {len(contacts)} contacts from '{exit_tag}' to list {destination_list}")
                
                # Use atomic operation for import
                operation_id = self.start_atomic_operation(
                    f"import_to_hubspot_list_{destination_list}",
                    {"exit_tag": exit_tag, "destination_list": destination_list, "contact_count": len(contacts)}
                )
                
                try:
                    imported_count = self.import_to_hubspot_list(contacts, destination_list, operation_id)
                    if imported_count > 0:
                        total_imported += imported_count
                        self.complete_atomic_operation(operation_id, success=True)
                        self.log(f"✅ Successfully imported {imported_count} contacts to list {destination_list}")
                    else:
                        self.log(f"⚠️ No contacts imported to list {destination_list}")
                        self.complete_atomic_operation(operation_id, success=False)
                        
                except Exception as e:
                    self.log(f"❌ Error importing contacts to list {destination_list}: {e}")
                    self.complete_atomic_operation(operation_id, success=False)
                    import_success = False
                    self.stats["errors_encountered"] += 1
            
            self.stats["total_contacts_imported"] = total_imported
            
            if not import_success:
                self.log("❌ Import phase failed - initiating rollback")
                if not self.rollback_all_operations():
                    self.log("❌ Rollback failed - system may be in inconsistent state")
                    self._send_final_notification(success=False, rollback_failed=True)
                    return
                else:
                    self.log("✅ Rollback successful - system state restored")
                    self._send_final_notification(success=False, rolled_back=True)
                    return
            
            # Phase 3: Anti-remarketing - Remove contacts from source lists
            self.log("\n🚫 Phase 3: Anti-remarketing - Removing contacts from source lists")
            self.log("-" * 60)
            
            removal_success = True
            total_removed = 0
            
            # Use atomic operation for removals
            removal_operation_id = self.start_atomic_operation(
                "anti_remarketing_removal",
                {"total_contacts": total_contacts_found, "mappings": config.SECONDARY_SYNC_MAPPINGS}
            )
            
            try:
                removed_count = self.remove_from_source_lists(all_exit_contacts, removal_operation_id)
                total_removed = removed_count
                self.complete_atomic_operation(removal_operation_id, success=True)
                self.log(f"✅ Anti-remarketing complete: {removed_count} total removals")
                
            except Exception as e:
                self.log(f"❌ Error during anti-remarketing: {e}")
                self.complete_atomic_operation(removal_operation_id, success=False)
                removal_success = False
                self.stats["errors_encountered"] += 1
            
            self.stats["total_contacts_removed"] = total_removed
            
            # Phase 4: Optional archival of processed contacts from Mailchimp
            if config.ENABLE_MAILCHIMP_ARCHIVAL:
                self.log("\n📦 Phase 4: Archiving processed contacts from Mailchimp")
                self.log("-" * 50)
                
                archival_success = True
                total_archived = 0
                
                # Use atomic operation for archival (though archival is irreversible)
                archival_operation_id = self.start_atomic_operation(
                    "mailchimp_archival",
                    {"total_contacts": total_contacts_found, "archival_enabled": True}
                )
                
                try:
                    archived_count = self.archive_mailchimp_contacts(all_exit_contacts, archival_operation_id)
                    total_archived = archived_count
                    self.complete_atomic_operation(archival_operation_id, success=True)
                    self.log(f"✅ Archival complete: {archived_count} contacts archived from Mailchimp")
                    
                except Exception as e:
                    self.log(f"❌ Error during archival: {e}")
                    self.complete_atomic_operation(archival_operation_id, success=False)
                    archival_success = False
                    self.stats["errors_encountered"] += 1
                
                self.stats["total_contacts_archived"] = total_archived
            else:
                self.log("\n📦 Phase 4: Mailchimp archival disabled - skipping")
                archival_success = True
            
            # Phase 5: Verification of all operations
            self.log("\n🔍 Phase 5: Verification of migration operations")
            self.log("-" * 45)
            
            verification_result = self.run_migration_verification()
            
            # Determine overall success
            overall_success = (import_success and removal_success and archival_success and 
                             verification_result.get("verification_success", False))
            
            if overall_success:
                self.log("✅ Secondary sync completed successfully!")
                self.log("="*60)
                self._send_final_notification(success=True)
            else:
                self.log("❌ Secondary sync completed with errors")
                self.log("="*60)
                
                # Check if errors exceed threshold for rollback
                error_count = self.stats.get("errors_encountered", 0)
                if error_count >= self.error_threshold:
                    self.log(f"❌ Error count ({error_count}) exceeds threshold ({self.error_threshold}) - initiating rollback")
                    if not self.rollback_all_operations():
                        self.log("❌ Rollback failed - system may be in inconsistent state")
                        self._send_final_notification(success=False, rollback_failed=True)
                    else:
                        self.log("✅ Rollback successful - system state restored")
                        self._send_final_notification(success=False, rolled_back=True)
                else:
                    self.log(f"⚠️ Errors within threshold - sync marked as partially successful")
                    self._send_final_notification(success=False, partial_success=True)
            
        except Exception as e:
            self.log("❌ Critical error in secondary sync workflow")
            self.stats["errors_encountered"] += 1
            
            # Attempt emergency rollback
            try:
                self.log("🆘 Attempting emergency rollback due to critical error")
                if self.rollback_all_operations():
                    self.log("✅ Emergency rollback successful")
                    self._send_final_notification(success=False, emergency_rollback=True)
                else:
                    self.log("❌ Emergency rollback failed")
                    self._send_final_notification(success=False, rollback_failed=True)
            except Exception as rollback_error:
                self.log("❌ Emergency rollback also failed")
                self._send_final_notification(success=False, rollback_failed=True)
            
            raise

    def _send_final_notification(self, success: bool, no_contacts: bool = False, 
                               rolled_back: bool = False, rollback_failed: bool = False,
                               emergency_rollback: bool = False, partial_success: bool = False):
        """Send final notification about secondary sync completion."""
        try:
            self.stats["sync_end_time"] = datetime.now()
            
            if no_contacts:
                status = "completed_no_contacts"
                message = "Secondary sync completed - no exit-tagged contacts found"
            elif success:
                status = "completed_successfully"
                message = "Secondary sync completed successfully"
            elif emergency_rollback:
                status = "failed_emergency_rollback"
                message = "Secondary sync failed with critical error - emergency rollback performed"
            elif rolled_back:
                status = "failed_rolled_back"
                message = "Secondary sync failed - operations rolled back successfully"
            elif rollback_failed:
                status = "failed_rollback_failed"
                message = "Secondary sync failed - rollback also failed"
            elif partial_success:
                status = "completed_with_errors"
                message = "Secondary sync completed with errors within threshold"
            else:
                status = "failed"
                message = "Secondary sync failed"
            
            # Add rollback status to stats
            rollback_status = self.get_rollback_status()
            self.stats.update({
                "rollback_metrics": rollback_status["rollback_metrics"],
                "active_operations": rollback_status["active_operations"]
            })
            
            notifications.notify_info(message, {
                "status": status,
                "stats": self.stats,
                "rollback_status": rollback_status
            })
            
        except Exception as e:
            self.log(f"Failed to send final notification: {e}", "WARN")

# =============================================================================
# 🚀 MODULE ENTRY POINT
# =============================================================================

def main():
    """Module entry point for secondary sync execution."""
    sync = MailchimpToHubSpotSync()
    sync.main()

if __name__ == "__main__":
    main()
