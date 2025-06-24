#!/usr/bin/env python3
"""
list_manager.py

HubSpot list management operations for bidirectional sync system.
Handles contact addition, removal, and list membership operations with
anti-remarketing controls.
"""

import requests
import time
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple

from . import config


class HubSpotListManager:
    """Manages HubSpot list operations for sync system"""
    
    def __init__(self):
        """Initialize the list manager"""
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {config.HUBSPOT_PRIVATE_TOKEN}',
            'Content-Type': 'application/json',
            'User-Agent': 'HubSpot-Mailchimp-Sync/2.0'
        })
        
        self.base_url = "https://api.hubapi.com"
        
        # API rate limiting
        self.max_retries = config.MAX_RETRIES
        self.retry_delay = config.RETRY_DELAY
    
    def log(self, message: str, level: str = "INFO"):
        """Log message with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [LIST_MGR] [{level}] {message}")
    
    def get_list_info(self, list_id: str) -> Optional[Dict]:
        """Get information about a HubSpot list"""
        url = f"{self.base_url}/contacts/v1/lists/{list_id}"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Error getting list info for {list_id}: {str(e)}", "ERROR")
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 🚀 V3 LISTS API BREAKTHROUGH METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    # These methods use the newly discovered /crm/v3/lists endpoints that 
    # enable full static list modification via API with raw JSON array payloads
    # ═══════════════════════════════════════════════════════════════════════════

    def get_list_info_v3(self, list_id: str) -> Optional[Dict]:
        """
        Get list information using v3 Lists API
        
        Returns list details including processingType to validate if list is MANUAL (static)
        
        Args:
            list_id: HubSpot list ID
            
        Returns:
            Dict with list info including name, processingType, size, etc.
            None if list not found or error occurred
        """
        url = f"{self.base_url}/crm/v3/lists/{list_id}"
        
        try:
            self.log(f"🔍 Getting v3 list info for list {list_id}")
            response = self.session.get(url)
            response.raise_for_status()
            
            raw = response.json()
            data = raw.get("list", raw)  # Unwrap "list" field if present
            
            # v3 Lists API returns metadata at top-level, not nested under "list"
            processing_type = data.get("processingType")
            name = data.get("listName")  # Direct field, not nested
            size = data.get("size")
            
            # Create unified format for compatibility
            list_data = {
                "name": name,
                "listName": name,  # For backward compatibility
                "processingType": processing_type,
                "size": size,
                "processingStatus": data.get("processingStatus"),
                "listId": data.get("listId")
            }
            
            self.log(f"✅ v3 List info retrieved: {name} "
                    f"({processing_type}, {size} contacts)")
            
            return list_data
            
        except Exception as e:
            self.log(f"❌ Error getting v3 list info for {list_id}: {str(e)}", "ERROR")
            return None

    def get_list_memberships_v3(self, list_id: str, limit: int = 100) -> List[str]:
        """
        Get contact IDs from a list using v3 Lists API
        
        Supports pagination for large lists (>100 contacts)
        
        Args:
            list_id: HubSpot list ID
            limit: Max contacts per page (default 100, HubSpot recommended max)
            
        Returns:
            List of contact ID strings
        """
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships"
        all_contact_ids = []
        after_cursor = None
        
        try:
            self.log(f"📋 Getting v3 list memberships for list {list_id}")
            
            while True:
                params = {"limit": limit}
                if after_cursor:
                    params["after"] = after_cursor
                
                response = self.session.get(url, params=params)
                response.raise_for_status()
                
                data = response.json()
                contacts = data.get("results", [])
                
                # Extract contact IDs
                page_contact_ids = [contact.get("recordId") for contact in contacts 
                                  if contact.get("recordId")]
                all_contact_ids.extend(page_contact_ids)
                
                # Check for next page
                paging = data.get("paging", {})
                if not paging.get("next"):
                    break
                after_cursor = paging["next"]["after"]
                
                self.log(f"📄 Retrieved page: {len(page_contact_ids)} contacts "
                        f"(total: {len(all_contact_ids)})")
            
            self.log(f"✅ v3 Memberships retrieved: {len(all_contact_ids)} total contacts")
            return all_contact_ids
            
        except Exception as e:
            self.log(f"❌ Error getting v3 list memberships for {list_id}: {str(e)}", "ERROR")
            return []

    def add_contact_to_list(self, contact_id: str, list_id: str) -> bool:
        """
        Add a contact to a HubSpot list using v3 Lists API for consistency
        
        Note: This replaces the previous multi-API approach to ensure consistency
        with batch operations and avoid deprecated legacy methods.
        """
        # Use v3 Lists API endpoint for consistency with batch operations
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships/add"
        
        # Use same format as batch method: raw array of contact ID strings
        payload = [str(contact_id)]
        
        self.log(f"🔄 Adding contact {contact_id} to list {list_id} (v3 API)")
        
        for attempt in range(self.max_retries):
            try:
                # PUT request with JSON array payload (same as batch_add_contacts_v3)
                response = self.session.put(url, json=payload)
                
                if response.status_code in (200, 201, 204):
                    self.log(f"✅ Added contact {contact_id} to list {list_id} (v3 API)")
                    return True
                elif response.status_code == 404:
                    self.log(f"❌ List {list_id} not found or contact {contact_id} doesn't exist", "ERROR")
                    return False
                else:
                    self.log(f"⚠️ Unexpected response adding contact to list: {response.status_code}", "WARN")
                    self.log(f"⚠️ Response: {response.text}", "WARN")
                    
                    if attempt < self.max_retries - 1:
                        self.log(f"⚠️ Retry {attempt + 1}/{self.max_retries} for adding contact to list", "WARN")
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False
                
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    self.log(f"⚠️ Retry {attempt + 1}/{self.max_retries} for adding contact to list", "WARN")
                    time.sleep(self.retry_delay)
                    continue
                else:
                    self.log(f"❌ Failed to add contact {contact_id} to list {list_id}: {str(e)}", "ERROR")
                    return False
        
        return False
    
    # =========================================================================
    # DEPRECATED METHODS - Kept for backward compatibility only
    # =========================================================================
    
    def _add_contact_to_list_v1(self, contact_id: str, list_id: str) -> bool:
        """
        DEPRECATED: Legacy v1 API method for adding contacts to lists
        
        This method is deprecated in favor of the v3 API used in add_contact_to_list().
        Use add_contact_to_list() instead for all new implementations.
        """
        self.log("⚠️ Using deprecated v1 API method - consider migrating to v3 API", "WARN")
        
        url = f"{self.base_url}/contacts/v1/lists/{list_id}/add"
        
        payload = {
            "vids": [int(contact_id)]
        }
        
        try:
            response = self.session.post(url, json=payload)
            
            if response.status_code in [200, 201, 204]:
                self.log(f"✅ Added contact {contact_id} to list {list_id} (deprecated v1 API)")
                return True
            elif response.status_code == 404:
                # List doesn't exist in v1 API - it's a v3-only list
                self.log(f"📝 List {list_id} not available in v1 API (v3-only list)")
                return False
            else:
                self.log(f"❌ v1 API failed: Status {response.status_code} - {response.text[:200]}", "ERROR")
                return False
                
        except Exception as e:
            self.log(f"❌ v1 API exception: {str(e)}", "ERROR")
            return False
    
    def _add_contact_to_list_via_property(self, contact_id: str, list_id: str) -> bool:
        """
        DEPRECATED: Property-based workaround for v3-only lists
        
        This method is deprecated in favor of the v3 API used in add_contact_to_list().
        The direct v3 Lists API is now used instead of property-based workarounds.
        
        This approach required creating a HubSpot workflow that:
        1. Triggers when list_membership_request property is set
        2. Parses the list ID from the property value  
        3. Adds the contact to the specified list
        """
        self.log("⚠️ Using deprecated property-based workaround - consider migrating to v3 API", "WARN")
        
        # Set a custom property that a HubSpot workflow can react to
        property_name = "list_membership_request"
        property_value = f"add_to_list_{list_id}"
        # HubSpot expects timestamp as milliseconds since epoch for datetime fields
        timestamp_ms = int(datetime.now().timestamp() * 1000)
        
        url = f"{self.base_url}/crm/v3/objects/contacts/{contact_id}"
        
        payload = {
            "properties": {
                property_name: property_value,
                "list_membership_timestamp": str(timestamp_ms),
                "list_membership_source": "mailchimp_secondary_sync"
            }
        }
        
        try:
            response = self.session.patch(url, json=payload)
            
            if response.status_code in [200, 204]:
                self.log(f"✅ Set list membership property for contact {contact_id} → list {list_id}")
                self.log(f"📝 Property: {property_name} = {property_value}")
                self.log(f"⚠️ NOTE: This requires a HubSpot workflow to complete the list addition", "WARN")
                return True
            else:
                self.log(f"❌ Failed to set property: {response.status_code} - {response.text}", "ERROR")
                return False
                
        except Exception as e:
            self.log(f"❌ Property update exception: {str(e)}", "ERROR")
            return False
    
    def _add_contact_to_list_v1_fallback(self, contact_id: str, list_id: str) -> bool:
        """Fallback to v1 API for older Static Lists"""
        url = f"{self.base_url}/contacts/v1/lists/{list_id}/add"
        
        payload = {
            "vids": [int(contact_id)]
        }
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.post(url, json=payload)
                response.raise_for_status()
                
                self.log(f"✅ Added contact {contact_id} to list {list_id} (v1 fallback)")
                return True
                
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    self.log(f"⚠️ Retry {attempt + 1}/{self.max_retries} for v1 fallback adding contact to list", "WARN")
                    time.sleep(self.retry_delay)
                    continue
                else:
                    self.log(f"❌ Failed to add contact {contact_id} to list {list_id} with v1 fallback: {str(e)}", "ERROR")
                    return False
        
        return False
    
    def remove_contact_from_list(self, contact_id: str, list_id: str) -> bool:
        """Remove a contact from a HubSpot list using v3 Lists API"""
        # Use v3 Lists API endpoint for consistency with batch operations
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships/remove"
        
        # Use same format as batch method: raw array of contact ID strings
        payload = [str(contact_id)]
        
        for attempt in range(self.max_retries):
            try:
                # PUT request with JSON array payload (same as batch_remove_contacts_v3)
                response = self.session.put(url, json=payload)
                
                if response.status_code in (200, 204):
                    self.log(f"✅ Removed contact {contact_id} from list {list_id} (v3 API)")
                    return True
                elif response.status_code == 404:
                    # Contact not in list or list doesn't exist - consider success
                    self.log(f"📝 Contact {contact_id} not found in list {list_id} (already removed)")
                    return True
                else:
                    self.log(f"⚠️ Unexpected response removing contact from list: {response.status_code}", "WARN")
                    self.log(f"⚠️ Response: {response.text}", "WARN")
                    
                    if attempt < self.max_retries - 1:
                        self.log(f"⚠️ Retry {attempt + 1}/{self.max_retries} for removing contact from list", "WARN")
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return False
                
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    self.log(f"⚠️ Retry {attempt + 1}/{self.max_retries} for removing contact from list", "WARN")
                    time.sleep(self.retry_delay)
                    continue
                else:
                    self.log(f"❌ Failed to remove contact {contact_id} from list {list_id}: {str(e)}", "ERROR")
                    return False
        
        return False
    
    def batch_add_contacts_to_list(self, contact_ids: List[str], list_id: str) -> Tuple[int, int]:
        """
        Add multiple contacts to a list in batches
        Returns: (success_count, error_count)
        """
        if not contact_ids:
            return 0, 0
        
        self.log(f"📥 Adding {len(contact_ids)} contacts to list {list_id} in batches")
        
        # HubSpot allows up to 1000 contacts per batch
        batch_size = 1000
        success_count = 0
        error_count = 0
        
        for i in range(0, len(contact_ids), batch_size):
            batch = contact_ids[i:i + batch_size]
            batch_vids = [int(cid) for cid in batch]
            
            url = f"{self.base_url}/contacts/v1/lists/{list_id}/add"
            payload = {"vids": batch_vids}
            
            try:
                response = self.session.post(url, json=payload)
                response.raise_for_status()
                
                success_count += len(batch)
                self.log(f"   ✅ Batch {i//batch_size + 1}: Added {len(batch)} contacts")
                
            except Exception as e:
                error_count += len(batch)
                self.log(f"   ❌ Batch {i//batch_size + 1}: Failed to add {len(batch)} contacts: {str(e)}", "ERROR")
        
        return success_count, error_count
    
    def batch_remove_contacts_from_list(self, contact_ids: List[str], list_id: str) -> Tuple[int, int]:
        """
        Remove multiple contacts from a list in batches
        Returns: (success_count, error_count)
        """
        if not contact_ids:
            return 0, 0
        
        self.log(f"🗑️ Removing {len(contact_ids)} contacts from list {list_id} in batches")
        
        # HubSpot allows up to 1000 contacts per batch
        batch_size = 1000
        success_count = 0
        error_count = 0
        
        for i in range(0, len(contact_ids), batch_size):
            batch = contact_ids[i:i + batch_size]
            batch_vids = [int(cid) for cid in batch]
            
            url = f"{self.base_url}/contacts/v1/lists/{list_id}/remove"
            payload = {"vids": batch_vids}
            
            try:
                response = self.session.post(url, json=payload)
                response.raise_for_status()
                
                success_count += len(batch)
                self.log(f"   ✅ Batch {i//batch_size + 1}: Removed {len(batch)} contacts")
                
            except Exception as e:
                error_count += len(batch)
                self.log(f"   ❌ Batch {i//batch_size + 1}: Failed to remove {len(batch)} contacts: {str(e)}", "ERROR")
        
        return success_count, error_count
    
    def apply_anti_remarketing_rules(self, contact_id: str, target_list_id: str) -> Dict[str, bool]:
        """
        Apply anti-remarketing rules for a contact
        Remove contact from source lists when they're added to target lists
        
        Returns: Dict mapping source_list_id -> removal_success
        """
        results = {}
        
        # Get current list memberships
        current_lists = self.get_list_memberships(contact_id)
        
        # Apply exclusion rules
        for source_list_id, exclusion_list_ids in config.LIST_EXCLUSION_RULES.items():
            if target_list_id in exclusion_list_ids and source_list_id in current_lists:
                # Contact is in a source list and should be removed
                success = self.remove_contact_from_list(contact_id, source_list_id)
                results[source_list_id] = success
                
                if success:
                    self.log(f"🚫 Anti-remarketing: Removed contact {contact_id} from source list {source_list_id}")
                else:
                    self.log(f"⚠️ Anti-remarketing: Failed to remove contact {contact_id} from source list {source_list_id}", "WARN")
        
        return results
    
    def validate_list_exists(self, list_id: str) -> bool:
        """Validate that a HubSpot list exists"""
        list_info = self.get_list_info(list_id)
        return list_info is not None
    
    def get_list_contact_count(self, list_id: str) -> Optional[int]:
        """Get the current contact count for a list"""
        list_info = self.get_list_info(list_id)
        if list_info:
            return list_info.get('metaData', {}).get('size', 0)
        return None
    
    def validate_secondary_sync_configuration(self) -> Tuple[bool, List[str]]:
        """
        Validate that all lists in secondary sync configuration exist
        Returns: (all_valid, list_of_errors)
        """
        self.log("🔍 Validating secondary sync list configuration...")
        
        errors = []
        all_valid = True
        
        # Check all target lists in secondary sync mappings
        for exit_tag, target_list_id in config.SECONDARY_SYNC_MAPPINGS.items():
            if not self.validate_list_exists(target_list_id):
                error_msg = f"Target list {target_list_id} (for tag '{exit_tag}') does not exist"
                errors.append(error_msg)
                all_valid = False
                self.log(f"❌ {error_msg}", "ERROR")
            else:
                self.log(f"✅ Target list {target_list_id} (for tag '{exit_tag}') exists")
        
        # Check all source lists in exclusion rules
        for source_list_id, exclusion_lists in config.LIST_EXCLUSION_RULES.items():
            if not self.validate_list_exists(source_list_id):
                error_msg = f"Source list {source_list_id} in exclusion rules does not exist"
                errors.append(error_msg)
                all_valid = False
                self.log(f"❌ {error_msg}", "ERROR")
            else:
                self.log(f"✅ Source list {source_list_id} exists")
            
            # Check exclusion target lists
            for exclusion_list_id in exclusion_lists:
                if not self.validate_list_exists(exclusion_list_id):
                    error_msg = f"Exclusion target list {exclusion_list_id} does not exist"
                    errors.append(error_msg)
                    all_valid = False
                    self.log(f"❌ {error_msg}", "ERROR")
        
        if all_valid:
            self.log("✅ All lists in secondary sync configuration are valid")
        else:
            self.log(f"❌ Found {len(errors)} list configuration errors")
        
        return all_valid, errors
    
    def get_configuration_summary(self) -> Dict:
        """Get summary of list configuration for reporting"""
        summary = {
            'secondary_sync_mappings': {},
            'exclusion_rules': {},
            'total_target_lists': len(config.SECONDARY_SYNC_MAPPINGS),
            'total_source_lists': len(config.LIST_EXCLUSION_RULES)
        }
        
        # Get target list details
        for exit_tag, target_list_id in config.SECONDARY_SYNC_MAPPINGS.items():
            list_info = self.get_list_info(target_list_id)
            summary['secondary_sync_mappings'][exit_tag] = {
                'target_list_id': target_list_id,
                'list_name': list_info.get('name', 'Unknown') if list_info else 'Not Found',
                'contact_count': self.get_list_contact_count(target_list_id)
            }
        
        # Get source list details
        for source_list_id, exclusion_lists in config.LIST_EXCLUSION_RULES.items():
            list_info = self.get_list_info(source_list_id)
            summary['exclusion_rules'][source_list_id] = {
                'list_name': list_info.get('name', 'Unknown') if list_info else 'Not Found',
                'contact_count': self.get_list_contact_count(source_list_id),
                'exclusion_target_count': len(exclusion_lists),
                'exclusion_targets': exclusion_lists
            }
        
        return summary
    
    def create_list_snapshot(self, list_ids: List[str]) -> Dict[str, Dict]:
        """Create a snapshot of list states for audit purposes"""
        timestamp = datetime.now().isoformat()
        snapshot = {
            'timestamp': timestamp,
            'lists': {}
        }
        
        for list_id in list_ids:
            list_info = self.get_list_info(list_id)
            if list_info:
                snapshot['lists'][list_id] = {
                    'name': list_info.get('name', 'Unknown'),
                    'contact_count': list_info.get('metaData', {}).get('size', 0),
                    'list_type': list_info.get('listType', 'Unknown'),
                    'created_at': list_info.get('createdAt', None),
                    'updated_at': list_info.get('updatedAt', None)
                }
            else:
                snapshot['lists'][list_id] = {
                    'error': 'List not found or inaccessible'
                }
        
        return snapshot
    
    def get_list_members(self, list_id: str) -> List[Dict]:
        """
        Get all members of a HubSpot list
        Returns: List of contact dictionaries with ID and basic info
        """
        self.log(f"Getting members of list {list_id}")
        
        # Try v3 API first for list memberships
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships"
        all_members = []
        
        try:
            # Get list of contact IDs in the list
            response = self.session.get(url)
            response.raise_for_status()
            
            data = response.json()
            member_records = data.get('results', [])
            
            if not member_records:
                self.log(f"No members found in list {list_id}")
                return []
            
            # Get contact details for each member
            contact_ids = [record['recordId'] for record in member_records]
            self.log(f"Found {len(contact_ids)} members, getting contact details...")
            
            # Batch get contact details
            for contact_id in contact_ids:
                contact_info = self._get_contact_basic_info(contact_id)
                if contact_info:
                    all_members.append(contact_info)
                    
            self.log(f"Retrieved details for {len(all_members)} contacts")
            return all_members
            
        except Exception as e:
            self.log(f"Error getting list members for {list_id}: {str(e)}", "ERROR")
            return []
    
    def _get_contact_basic_info(self, contact_id: str) -> Optional[Dict]:
        """Get basic contact information (ID, email, name)"""
        url = f"{self.base_url}/crm/v3/objects/contacts/{contact_id}"
        params = {
            'properties': 'email,firstname,lastname,company'
        }
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            properties = data.get('properties', {})
            
            return {
                'id': contact_id,
                'email': properties.get('email', ''),
                'firstname': properties.get('firstname', ''),
                'lastname': properties.get('lastname', ''),
                'company': properties.get('company', '')
            }
            
        except Exception as e:
            self.log(f"Error getting contact {contact_id}: {str(e)}", "ERROR")
            return None
    
    def batch_add_contacts_v3(self, list_id: str, contact_ids: List[str], 
                             batch_size: int = 100) -> Dict[str, any]:
        """
        Add multiple contacts to a static list using v3 Lists API
        
        Uses proven bulk endpoint with raw JSON array payload format
        
        Args:
            list_id: HubSpot list ID
            contact_ids: List of contact ID strings
            batch_size: Max contacts per API call (default 100)
            
        Returns:
            Dict with success status and details:
            {
                "success": bool,
                "total_added": int,
                "failed_batches": List[Dict],
                "api_responses": List[Dict]
            }
        """
        if not contact_ids:
            return {"success": True, "total_added": 0, "failed_batches": [], "api_responses": []}
        
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships/add"
        total_added = 0
        failed_batches = []
        api_responses = []
        
        self.log(f"➕ Adding {len(contact_ids)} contacts to list {list_id} using v3 API")
        
        # Process in batches
        for i in range(0, len(contact_ids), batch_size):
            batch = contact_ids[i:i + batch_size]
            batch_payload = [str(cid) for cid in batch]  # Raw array of strings - CRITICAL FORMAT
            
            try:
                self.log(f"📦 Processing batch {i//batch_size + 1}: {len(batch)} contacts")
                
                response = self.session.put(url, json=batch_payload)
                response.raise_for_status()
                
                response_data = response.json()
                api_responses.append({
                    "batch_index": i//batch_size + 1,
                    "status_code": response.status_code,
                    "response": response_data
                })
                
                # Count successfully added contacts
                added_ids = response_data.get("recordsIdsAdded", [])
                total_added += len(added_ids)
                
                self.log(f"✅ Batch {i//batch_size + 1} success: {len(added_ids)} contacts added")
                
            except Exception as e:
                error_info = {
                    "batch_index": i//batch_size + 1,
                    "batch_size": len(batch),
                    "error": str(e)
                }
                failed_batches.append(error_info)
                self.log(f"❌ Batch {i//batch_size + 1} failed: {str(e)}", "ERROR")
        
        success = len(failed_batches) == 0
        self.log(f"📊 v3 Bulk add complete: {total_added}/{len(contact_ids)} contacts added "
                f"({len(failed_batches)} batches failed)")
        
        return {
            "success": success,
            "total_added": total_added,
            "failed_batches": failed_batches,
            "api_responses": api_responses
        }

    def batch_remove_contacts_v3(self, list_id: str, contact_ids: List[str], 
                                  batch_size: int = 100) -> Dict[str, any]:
        """
        Remove multiple contacts from a static list using v3 Lists API
        
        Uses proven bulk endpoint with raw JSON array payload format
        
        Args:
            list_id: HubSpot list ID
            contact_ids: List of contact ID strings
            batch_size: Max contacts per API call (default 100)
            
        Returns:
            Dict with success status and details:
            {
                "success": bool,
                "total_removed": int,
                "failed_batches": List[Dict],
                "api_responses": List[Dict]
            }
        """
        if not contact_ids:
            return {"success": True, "total_removed": 0, "failed_batches": [], "api_responses": []}
        
        url = f"{self.base_url}/crm/v3/lists/{list_id}/memberships/remove"
        total_removed = 0
        failed_batches = []
        api_responses = []
        
        self.log(f"➖ Removing {len(contact_ids)} contacts from list {list_id} using v3 API")
        
        # Process in batches
        for i in range(0, len(contact_ids), batch_size):
            batch = contact_ids[i:i + batch_size]
            batch_payload = [str(cid) for cid in batch]  # Raw array of strings - CRITICAL FORMAT
            
            try:
                self.log(f"📦 Processing batch {i//batch_size + 1}: {len(batch)} contacts")
                
                response = self.session.put(url, json=batch_payload)
                response.raise_for_status()
                
                response_data = response.json()
                api_responses.append({
                    "batch_index": i//batch_size + 1,
                    "status_code": response.status_code,
                    "response": response_data
                })
                
                # Count successfully removed contacts
                removed_ids = response_data.get("recordIdsRemoved", [])
                total_removed += len(removed_ids)
                
                self.log(f"✅ Batch {i//batch_size + 1} success: {len(removed_ids)} contacts removed")
                
            except Exception as e:
                error_info = {
                    "batch_index": i//batch_size + 1,
                    "batch_size": len(batch),
                    "error": str(e)
                }
                failed_batches.append(error_info)
                self.log(f"❌ Batch {i//batch_size + 1} failed: {str(e)}", "ERROR")
        
        success = len(failed_batches) == 0
        self.log(f"📊 v3 Bulk remove complete: {total_removed}/{len(contact_ids)} contacts removed "
                f"({len(failed_batches)} batches failed)")
        
        return {
            "success": success,
            "total_removed": total_removed,
            "failed_batches": failed_batches,
            "api_responses": api_responses
        }

    # ═══════════════════════════════════════════════════════════════════════════

    def validate_list_for_migration_v3(self, list_id: str) -> Dict[str, any]:
        """
        Pre-flight validation for list migration using v3 API
        
        Validates that list exists and is MANUAL (static) type for modification
        
        Args:
            list_id: HubSpot list ID to validate
            
        Returns:
            Dict with validation results:
            {
                "valid": bool,
                "list_info": Dict or None,
                "errors": List[str]
            }
        """
        errors = []
        
        # Get list info
        list_info = self.get_list_info_v3(list_id)
        
        if not list_info:
            errors.append(f"List {list_id} not found or inaccessible")
            return {"valid": False, "list_info": None, "errors": errors}
        
        # Validate list type
        processing_type = list_info.get("processingType")
        if processing_type != "MANUAL":
            errors.append(f"List {list_id} is {processing_type} type, not MANUAL (static)")
        
        # Validate list is active
        status = list_info.get("processingStatus")
        if status != "COMPLETE":
            errors.append(f"List {list_id} status is {status}, not COMPLETE")
        
        is_valid = len(errors) == 0
        
        if is_valid:
            self.log(f"✅ List {list_id} validation passed: {list_info.get('name')} "
                    f"({processing_type}, {list_info.get('size')} contacts)")
        else:
            self.log(f"❌ List {list_id} validation failed: {', '.join(errors)}", "ERROR")
        
        return {
            "valid": is_valid,
            "list_info": list_info,
            "errors": errors
        }
    
    # =============================================================================
    # 🔍 PHASE 3: MIGRATION VERIFICATION & AUDIT METHODS
    # =============================================================================
    
    def verify_migration_with_delay(self, source_list_id: str, target_list_id: str, 
                                   expected_contact_ids: List[str], 
                                   delay_seconds: int = 300,
                                   max_retries: int = 3) -> Dict[str, any]:
        """
        Verify migration with intelligent delay handling for HubSpot propagation
        
        Args:
            source_list_id: Source list to check for removal
            target_list_id: Target list to check for addition
            expected_contact_ids: Contact IDs that should have migrated
            delay_seconds: Wait time before verification (default 5 minutes)
            max_retries: Maximum verification attempts
            
        Returns:
            Dict with verification results and audit information
        """
        import time
        
        verification_id = f"migration_{int(time.time())}"
        
        self.log(f"🔍 Starting delayed migration verification (ID: {verification_id})")
        self.log(f"   Source list: {source_list_id}")
        self.log(f"   Target list: {target_list_id}")
        self.log(f"   Expected contacts: {len(expected_contact_ids)}")
        self.log(f"   Delay: {delay_seconds} seconds ({delay_seconds//60} minutes)")
        self.log(f"   Max retries: {max_retries}")
        
        # Capture initial state for audit
        audit_log = {
            "verification_id": verification_id,
            "timestamp_start": datetime.now().isoformat(),
            "source_list_id": source_list_id,
            "target_list_id": target_list_id,
            "expected_contact_ids": expected_contact_ids,
            "delay_seconds": delay_seconds,
            "max_retries": max_retries,
            "attempts": []
        }
        
        # Wait for HubSpot propagation
        self.log(f"⏳ Waiting {delay_seconds} seconds for HubSpot propagation...")
        time.sleep(delay_seconds)
        
        # Retry verification with exponential backoff
        for attempt in range(1, max_retries + 1):
            self.log(f"🔍 Verification attempt {attempt}/{max_retries}")
            
            attempt_start = datetime.now()
            attempt_data = {
                "attempt_number": attempt,
                "timestamp": attempt_start.isoformat(),
                "success": False,
                "errors": []
            }
            
            try:
                # Check source list (contacts should be removed)
                source_contacts = self.get_list_memberships_v3(source_list_id)
                source_has_contacts = any(contact_id in source_contacts for contact_id in expected_contact_ids)
                
                # Check target list (contacts should be added)
                target_contacts = self.get_list_memberships_v3(target_list_id)
                target_has_contacts = all(contact_id in target_contacts for contact_id in expected_contact_ids)
                
                attempt_data.update({
                    "source_list_size": len(source_contacts),
                    "target_list_size": len(target_contacts),
                    "source_still_has_contacts": source_has_contacts,
                    "target_has_all_contacts": target_has_contacts
                })
                
                # Verification success criteria
                if not source_has_contacts and target_has_contacts:
                    self.log(f"✅ Migration verification successful on attempt {attempt}")
                    attempt_data["success"] = True
                    audit_log["attempts"].append(attempt_data)
                    audit_log["verification_success"] = True
                    audit_log["timestamp_end"] = datetime.now().isoformat()
                    
                    return {
                        "verified": True,
                        "attempts": attempt,
                        "audit_log": audit_log,
                        "verification_id": verification_id
                    }
                else:
                    # Log current state
                    if source_has_contacts:
                        attempt_data["errors"].append("Contacts still in source list")
                        self.log(f"   ⚠️ Some contacts still in source list {source_list_id}")
                    
                    if not target_has_contacts:
                        missing_count = len(expected_contact_ids) - sum(1 for cid in expected_contact_ids if cid in target_contacts)
                        attempt_data["errors"].append(f"Missing {missing_count} contacts in target list")
                        self.log(f"   ⚠️ {missing_count} contacts missing from target list {target_list_id}")
                
            except Exception as e:
                error_msg = f"Verification attempt failed: {str(e)}"
                attempt_data["errors"].append(error_msg)
                self.log(f"   ❌ {error_msg}", "ERROR")
            
            audit_log["attempts"].append(attempt_data)
            
            # Wait before retry (exponential backoff)
            if attempt < max_retries:
                wait_time = 60 * attempt  # 1 min, 2 min, 3 min...
                self.log(f"   ⏳ Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
        
        # All attempts failed
        self.log(f"❌ Migration verification failed after {max_retries} attempts", "ERROR")
        audit_log["verification_success"] = False
        audit_log["timestamp_end"] = datetime.now().isoformat()
        
        return {
            "verified": False,
            "attempts": max_retries,
            "audit_log": audit_log,
            "verification_id": verification_id
        }
    
    def create_migration_audit_report(self, audit_log: Dict) -> str:
        """
        Create detailed migration audit report
        
        Args:
            audit_log: Audit log from verification process
            
        Returns:
            Formatted audit report string
        """
        verification_id = audit_log.get("verification_id", "unknown")
        success = audit_log.get("verification_success", False)
        attempts = audit_log.get("attempts", [])
        
        status_emoji = "✅" if success else "❌"
        status_text = "SUCCESS" if success else "FAILED"
        
        report = f"""
{status_emoji} MIGRATION AUDIT REPORT
{'='*50}
Verification ID: {verification_id}
Status: {status_text}
Start Time: {audit_log.get('timestamp_start', 'unknown')}
End Time: {audit_log.get('timestamp_end', 'unknown')}

MIGRATION DETAILS:
Source List ID: {audit_log.get('source_list_id', 'unknown')}
Target List ID: {audit_log.get('target_list_id', 'unknown')}
Expected Contacts: {len(audit_log.get('expected_contact_ids', []))}
Delay Period: {audit_log.get('delay_seconds', 0)} seconds

VERIFICATION ATTEMPTS:
"""
        
        for i, attempt in enumerate(attempts, 1):
            attempt_status = "✅ PASS" if attempt.get("success") else "❌ FAIL"
            report += f"""
Attempt {i}: {attempt_status}
  Time: {attempt.get('timestamp', 'unknown')}
  Source List Size: {attempt.get('source_list_size', 'unknown')}
  Target List Size: {attempt.get('target_list_size', 'unknown')}
  Source Still Has Contacts: {attempt.get('source_still_has_contacts', 'unknown')}
  Target Has All Contacts: {attempt.get('target_has_all_contacts', 'unknown')}"""
            
            if attempt.get("errors"):
                report += f"\n  Errors: {', '.join(attempt['errors'])}"
        
        if success:
            final_attempt = attempts[-1] if attempts else {}
            report += f"""

✅ MIGRATION VERIFIED SUCCESSFULLY
Final State:
  • Source list contacts removed: ✅
  • Target list contacts added: ✅
  • Verification completed in {len(attempts)} attempt(s)
"""
        else:
            report += f"""

❌ MIGRATION VERIFICATION FAILED
After {len(attempts)} attempts, migration could not be verified.
This may indicate:
  • HubSpot propagation taking longer than expected
  • API issues preventing verification
  • Actual migration failure requiring investigation
"""
        
        return report
    
    def save_audit_log(self, audit_log: Dict, file_prefix: str = "migration_audit") -> str:
        """
        Save audit log to file for compliance and debugging
        
        Args:
            audit_log: Audit log data
            file_prefix: Prefix for audit file name
            
        Returns:
            Path to saved audit file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        verification_id = audit_log.get("verification_id", "unknown")
        filename = f"{file_prefix}_{verification_id}_{timestamp}.json"
        
        # Ensure audit directory exists
        import os
        audit_dir = "audit_logs"
        os.makedirs(audit_dir, exist_ok=True)
        
        filepath = os.path.join(audit_dir, filename)
        
        try:
            import json
            with open(filepath, 'w') as f:
                json.dump(audit_log, f, indent=2, default=str)
            
            self.log(f"💾 Audit log saved: {filepath}")
            return filepath
            
        except Exception as e:
            self.log(f"❌ Failed to save audit log: {str(e)}", "ERROR")
            return ""

def validate_configuration():
    """Standalone function to validate list configuration"""
    manager = HubSpotListManager()
    return manager.validate_secondary_sync_configuration()


def get_configuration_summary():
    """Standalone function to get configuration summary"""
    manager = HubSpotListManager()
    return manager.get_configuration_summary()


if __name__ == "__main__":
    # Run validation when called directly
    print("🔍 HubSpot List Manager - Configuration Validation")
    print("=" * 60)
    
    valid, errors = validate_configuration()
    
    if valid:
        print("✅ Configuration validation passed!")
        
        print("\n📊 Configuration Summary:")
        print("-" * 30)
        summary = get_configuration_summary()
        
        print(f"Target Lists: {summary['total_target_lists']}")
        for tag, details in summary['secondary_sync_mappings'].items():
            print(f"  • {tag} → {details['list_name']} ({details['contact_count']} contacts)")
        
        print(f"\nSource Lists: {summary['total_source_lists']}")
        for list_id, details in summary['exclusion_rules'].items():
            print(f"  • {details['list_name']} ({details['contact_count']} contacts)")
    else:
        print("❌ Configuration validation failed!")
        for error in errors:
            print(f"  • {error}")
