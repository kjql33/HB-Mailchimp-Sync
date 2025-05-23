#!/usr/bin/env python
"""
HubSpot → Mailchimp Sync Agent

This script synchronizes contacts from a HubSpot Active List to a Mailchimp Audience,
tagging them appropriately and removing contacts no longer in the HubSpot list.
"""

import sys, time, hashlib, logging, json
import os
from typing import Dict, List, Any, Optional
import requests

# centrally managed config
import config

def create_mailchimp_merge_field(tag: str, name: str, field_type: str = "text") -> None:
    """
    Create a new merge-field on the Mailchimp audience.
    """
    url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/merge-fields"
    payload = {
        "tag": tag,
        "name": name,
        "type": field_type,
        "public": False
    }
    resp = requests.post(url, auth=("anystring", MAILCHIMP_API_KEY), json=payload)
    resp.raise_for_status()
    logger.info(f"✅ Created Mailchimp merge-field: {tag}")

## Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("sync.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

## Pull configuration from config.py
HUBSPOT_PRIVATE_TOKEN  = config.HUBSPOT_PRIVATE_TOKEN
# list of list IDs to sync
HUBSPOT_LIST_IDS       = config.HUBSPOT_LIST_IDS
# Mailchimp settings
MAILCHIMP_API_KEY      = config.MAILCHIMP_API_KEY
MAILCHIMP_LIST_ID      = config.MAILCHIMP_LIST_ID
MAILCHIMP_DC           = config.MAILCHIMP_DC
# Mailchimp base URL
MAILCHIMP_BASE_URL     = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"

# paging, limits & retries from config
PAGE_SIZE              = config.PAGE_SIZE
TEST_CONTACT_LIMIT     = config.TEST_CONTACT_LIMIT
MAX_RETRIES            = config.MAX_RETRIES
RETRY_DELAY            = config.RETRY_DELAY

# merge-fields to enforce
REQUIRED_TAGS          = config.REQUIRED_TAGS

# runtime variables
MAILCHIMP_TAG          = None

# log level
logger.setLevel(config.LOG_LEVEL)


def validate_environment() -> bool:
    """Validate that all required configuration values are set in config.py."""
    missing = []
    if not HUBSPOT_PRIVATE_TOKEN:
        missing.append('HUBSPOT_PRIVATE_TOKEN')
    if not HUBSPOT_LIST_IDS:
        missing.append('HUBSPOT_LIST_IDS')
    if not MAILCHIMP_API_KEY:
        missing.append('MAILCHIMP_API_KEY')
    if not MAILCHIMP_LIST_ID:
        missing.append('MAILCHIMP_LIST_ID')
    if not MAILCHIMP_DC:
        missing.append('MAILCHIMP_DC')
    if missing:
        logger.error(f"Missing required config values: {', '.join(missing)}")
        return False
    return True


def get_hubspot_contacts(list_id: str) -> List[Dict[str, Any]]:
    """Fetch contacts from a specified HubSpot list using a two-step approach:
    
    1. First fetch all contact VIDs from the Lists API for the given list ID
    2. Then retrieve full contact details using the Batch Read API
    
    This approach works correctly with both Static and Active Lists.
    """
    if not HUBSPOT_PRIVATE_TOKEN:
        logger.error("HubSpot Private Token not set")
        return []
    
    if not list_id:
        logger.error("HubSpot List ID not provided")
        return []

    contacts = []
    
    # Truncate the raw contacts file at the start of a run
    with open("hubspot_raw_contacts.json", "w") as f:
        f.write(f"# HubSpot Raw Contacts from List ID {list_id} - Generated on {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    
    headers = {
        "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Define the properties we want to retrieve from the Batch Read API
    properties = ["email", "company", "phone", "city", "industry", "firstname", "lastname"]
    
    # STEP 1 (v3): Fetch contact IDs via CRM v3 Lists API
    logger.info(f"STEP 1 (v3): Fetching contact IDs from list {list_id}")
    list_url = f"https://api.hubapi.com/crm/v3/lists/{list_id}/memberships"
    # Page size from config
    params = {"limit": PAGE_SIZE}
    after = None
    all_vids: List[str] = []
    page = 1

    import math  # for expected page calculation
    expected_pages = None
    while True:
        if after:
            params["after"] = after

        logger.info(f"Fetching CRM v3 memberships page {page}")
        resp = requests.get(list_url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        # On first page, log total and expected page count
        if page == 1:
            total_members = body.get("total")
            if isinstance(total_members, int):
                expected_pages = math.ceil(total_members / params.get("limit", 1))
                logger.info(f"Total memberships in list: {total_members}, expecting ~{expected_pages} pages at {params['limit']} per page")

        # Dump raw response for debug
        with open(
            f"hubspot_list_{list_id}_memberships_page_{page}.json", "w"
        ) as f:
            json.dump(body, f, indent=2)

        # Each membership has recordId = contact’s internal ID
        results = body.get("results", [])
        vids = [m.get("recordId") for m in results if m.get("recordId")]
        logger.info(f"Retrieved {len(vids)} IDs from page {page}")
        all_vids.extend(vids)
        # Early exit if test cap reached
        if TEST_CONTACT_LIMIT > 0 and len(all_vids) >= TEST_CONTACT_LIMIT:
            logger.info(f"TEST_CONTACT_LIMIT={TEST_CONTACT_LIMIT} reached; ending STEP 1 early")
            all_vids = all_vids[:TEST_CONTACT_LIMIT]
            break
        # Pagination cursor
        paging = body.get("paging", {}).get("next", {})
        after = paging.get("after")
        if not after:
            logger.info("No further paging cursor returned – ending STEP 1")
            break

        page += 1
        time.sleep(1)  # respect rate limits

    logger.info(f"STEP 1 COMPLETE: collected {len(all_vids)} contact IDs")
    # apply test limit if set (>0)
    if TEST_CONTACT_LIMIT > 0 and len(all_vids) > TEST_CONTACT_LIMIT:
        logger.info(f"TEST_CONTACT_LIMIT={TEST_CONTACT_LIMIT} set; truncating to {TEST_CONTACT_LIMIT} IDs")
        all_vids = all_vids[:TEST_CONTACT_LIMIT]
    if not all_vids:
        logger.warning("No contacts found in CRM v3 list – aborting")
        return []
        
    # STEP 2: Retrieve contact details using the Batch Read API
    logger.info("STEP 2: Fetching contact details using Batch Read API")
    batch_url = "https://api.hubapi.com/crm/v3/objects/contacts/batch/read"
    
    # Process VIDs in batches of 100
    batch_size = 100
    vid_batches = [all_vids[i:i + batch_size] for i in range(0, len(all_vids), batch_size)]
    
    batch_counter = 1
    
    for vid_batch in vid_batches:
        logger.info(f"Processing batch {batch_counter} of {len(vid_batches)} with {len(vid_batch)} contacts")
        
        # Create batch read payload
        batch_payload = {
            "properties": properties,
            "inputs": [{"id": str(vid)} for vid in vid_batch]
        }
        
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    # Send batch request
                    logger.info(f"Sending Batch Read API request for {len(vid_batch)} contacts")
                    logger.debug(f"Batch Read payload: {json.dumps(batch_payload)}")
                    
                    response = requests.post(batch_url, headers=headers, json=batch_payload)
                    
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])
                        
                        # Log success info
                        logger.info(f"Successfully retrieved {len(results)} contact details from batch {batch_counter}")
                        
                        # Write raw contact data to debug file
                        if results:
                            with open("hubspot_raw_contacts.json", "a") as f:
                                f.write(f"\n\n# Batch {batch_counter} of {len(results)} contacts\n\n")
                                for contact in results:
                                    json.dump(contact, f, indent=2)
                                    f.write("\n\n---\n\n")
                                
                            # Process contacts in this batch
                            batch_contacts = []
                            for contact in results:
                                contact_properties = contact.get("properties", {})
                                
                                # Extract email (required field)
                                email = contact_properties.get("email", "")
                                
                                # Final check for email - only field we absolutely require
                                if not email:
                                    logger.warning(f"Skipping contact with missing email: ID {contact.get('id')}")
                                    # Log all missing properties
                                    missing_fields = [field for field in properties if field not in contact_properties or not contact_properties.get(field)]
                                    if missing_fields:
                                        logger.warning(f"Contact {contact.get('id')} is missing fields: {', '.join(missing_fields)}")
                                    continue  # Skip contacts without email
                                
                                # Extract required fields with empty string fallbacks
                                contact_data = {
                                    "email": email.lower(),  # Ensure email is lowercase
                                    "company": contact_properties.get("company", ""),
                                    "phone": contact_properties.get("phone", ""),
                                    "city": contact_properties.get("city", ""),
                                    "industry": contact_properties.get("industry", ""),
                                    "firstname": contact_properties.get("firstname", ""),
                                    "lastname": contact_properties.get("lastname", "")
                                }
                                
                                # Extract any additional useful contact info that might be available
                                contact_id = contact.get("id")
                                if contact_id:
                                    contact_data["hubspot_id"] = str(contact_id)
                                
                                # If we made it here, we have a valid contact with at least an email
                                batch_contacts.append(contact_data)
                            
                            contacts.extend(batch_contacts)
                            logger.info(f"Processed {len(batch_contacts)} valid contacts from batch {batch_counter}")
                    else:
                        logger.error(f"HubSpot Batch Read API error: Status {response.status_code}")
                        logger.error(f"Response: {response.text}")
                        
                    break  # Break out of retry loop on completed request
                    
                except requests.exceptions.RequestException as e:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(f"HubSpot API request failed: {e}. Retrying in {RETRY_DELAY} seconds...")
                        time.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"Error fetching contact details batch {batch_counter}: {e}")
                        # Continue to the next batch rather than aborting everything
            
            # Increment batch counter
            batch_counter += 1
            
            # Respect API rate limits
            time.sleep(1)  # 1 second between batches to avoid rate limiting
            
        except Exception as e:
            logger.error(f"Error processing contact batch {batch_counter}: {e}")
            # Continue to next batch rather than aborting everything
    
    # Summary report for the entire sync operation
    total_contacts = len(contacts)
    if total_contacts > 0:
        logger.info(f"Successfully retrieved {total_contacts} valid contacts from HubSpot list ID {list_id}")
        email_domains = set()
        for contact in contacts:
            if "@" in contact["email"]:
                email_domains.add(contact["email"].split('@')[1])
        logger.info(f"Contacts include email domains: {', '.join(list(email_domains)[:5])}{' and more...' if len(email_domains) > 5 else ''}")
    else:
        logger.warning(f"⚠️ No valid contacts retrieved from HubSpot list ID {list_id}")
        logger.warning("Please check that the list exists and contains contacts")
    
    return contacts


def get_contact_property(contact: Dict[str, Any], property_name: str) -> Optional[str]:
    """Helper to safely extract a property value from a HubSpot contact."""
    try:
        return contact.get("properties", {}).get(property_name, {}).get("value", "")
    except (AttributeError, KeyError):
        return None


def calculate_subscriber_hash(email: str) -> str:
    """Calculate MD5 hash of lowercase email address for Mailchimp API."""
    return hashlib.md5(email.lower().encode()).hexdigest()


def get_current_mailchimp_emails() -> Dict[str, str]:
    """
    Fetch all members from the Mailchimp audience that have the HubSpot tag.
    Returns a dict mapping email to subscriber_hash.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return {}
    
    mailchimp_members = {}
    
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members"
    params = {
        "count": 1000,  # Maximum allowed by Mailchimp
        "offset": 0
    }
    
    try:
        has_more = True
        
        while has_more:
            logger.info(f"Fetching Mailchimp members with offset: {params['offset']}")
            
            response = requests.get(url, auth=auth, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            for member in data.get("members", []):
                email = member.get("email_address", "").lower()
                subscriber_hash = member.get("id")
                
                # Check if this member has our tag
                tags = member.get("tags", [])
                if any(tag.get("name") == MAILCHIMP_TAG for tag in tags):
                    mailchimp_members[email] = subscriber_hash
            
            # Check if there are more members to fetch
            total_members = data.get("total_items", 0)
            current_count = params['offset'] + len(data.get("members", []))
            
            if current_count < total_members:
                params['offset'] = current_count
            else:
                has_more = False
                
            # Respect API rate limits
            if has_more:
                time.sleep(0.5)
                
    except Exception as e:
        logger.error(f"Error fetching Mailchimp members: {e}")
    
    logger.info(f"Found {len(mailchimp_members)} members in Mailchimp with the '{MAILCHIMP_TAG}' tag")
    return mailchimp_members


def upsert_mailchimp_contact(contact: Dict[str, str]) -> bool:
    """
    Add or update a contact in Mailchimp audience.
    Returns True if successful, False otherwise.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return False
    
    email = contact["email"].lower()
    subscriber_hash = calculate_subscriber_hash(email)
    
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    
    # Build merge fields, adding first and last name if they exist
    merge_fields = {
        "COMPANY": str(contact.get("company") or "")[:255],  # Ensure within Mailchimp field limits
        "PHONE": str(contact.get("phone") or "")[:50],
        "CITY": str(contact.get("city") or "")[:50],
        "INDUSTRY": str(contact.get("industry") or "")[:255]
    }
    
    # Add FNAME and LNAME if available
    if contact.get("firstname"):
        merge_fields["FNAME"] = str(contact.get("firstname"))[:50]
    
    if contact.get("lastname"):
        merge_fields["LNAME"] = str(contact.get("lastname"))[:50]
        
    data = {
        "email_address": email,
        "status_if_new": "subscribed",
        # FORCE-SUBSCRIBE / unarchive any existing member
        "status": "subscribed",
        "merge_fields": merge_fields
        # Tags are applied separately in a dedicated API call
    }
    
    # Log the full request payload for diagnostic purposes
    logger.debug(f"Mailchimp upsert request payload for {email}:")
    logger.debug(json.dumps(data, indent=2))
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.put(url, auth=auth, headers=headers, json=data)
                
                # Always log full response for debugging
                try:
                    response_body = response.json()
                    logger.debug(f"Mailchimp upsert response for {email}:")
                    logger.debug(json.dumps(response_body, indent=2))
                    
                    # Check for warnings, errors or special messages in response
                    if "errors" in response_body:
                        logger.warning(f"⚠️ Mailchimp responded with warnings for {email}: {response_body['errors']}")
                    if "detail" in response_body:
                        logger.warning(f"⚠️ Mailchimp returned detail message for {email}: {response_body['detail']}")
                        
                    # Log member status explicitly if available
                    if "status" in response_body:
                        logger.info(f"Contact {email} status after upsert: {response_body['status']}")
                        if response_body["status"] not in ["subscribed", "unsubscribed"]:
                            logger.warning(f"⚠️ Contact {email} has status '{response_body['status']}' which may not be visible in Mailchimp UI")
                except ValueError:
                    logger.debug(f"No JSON response body for {email}")
                
                # Handle successful responses
                if response.status_code in (200, 201):
                    logger.info(f"Successfully upserted contact: {email} (Status: {response.status_code})")
                    
                    # Apply tag with increased delay to ensure the member is fully created/updated
                    logger.debug(f"Waiting 2 seconds before applying tag to {email}")
                    time.sleep(2)  # Increased from 1s to 2s
                    if apply_mailchimp_tag(email):
                        logger.info(f"Successfully tagged {email} with '{MAILCHIMP_TAG}'")
                        
                        # Verify contact status after tagging
                        logger.debug(f"Verifying contact status for {email} after tagging")
                        contact_data = get_mailchimp_contact_status(email)
                        if not contact_data:
                            logger.error(f"❌ Failed to verify contact status for {email} after successful upsert and tagging")
                    else:
                        logger.warning(f"Failed to tag {email} - the contact was upserted but tagging failed")
                    
                    return True
                else:
                    response.raise_for_status()
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise
        
    except Exception as e:
        logger.error(f"Error upserting Mailchimp contact {email}: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
        return False
    
    return False


def apply_mailchimp_tag(email: str) -> bool:
    """
    Apply a tag to a Mailchimp contact.
    Returns True if successful, False otherwise.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return False
    
    email = email.lower()
    subscriber_hash = calculate_subscriber_hash(email)
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    tag_payload = {
        "tags": [{"name": MAILCHIMP_TAG, "status": "active"}]
    }
    
    logger.debug(f"Applying tag '{MAILCHIMP_TAG}' to {email}")
    logger.debug(f"URL: {url}")
    logger.debug(f"Payload: {json.dumps(tag_payload)}")
    
    # First, verify that the contact exists before trying to tag
    check_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    check_response = requests.get(check_url, auth=auth, headers=headers)
    
    if check_response.status_code == 404:
        logger.error(f"❌ Contact {email} not found in Mailchimp - cannot apply tag!")
        logger.error(f"This suggests the contact was not properly created despite successful upsert response")
        return False
    elif check_response.status_code == 200:
        member_data = check_response.json()
        logger.debug(f"Contact {email} exists with status '{member_data.get('status')}' before tagging")
        
        # Check if status would prevent visibility
        if member_data.get('status') not in ["subscribed", "unsubscribed"]:
            logger.warning(f"⚠️ Contact {email} has status '{member_data.get('status')}' which may affect visibility in UI")
    else:
        logger.warning(f"Unexpected response when checking if contact {email} exists: {check_response.status_code}")
        logger.warning(f"Response: {check_response.text}")
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(f"Sending tag request for {email} to Mailchimp")
                response = requests.post(url, auth=auth, headers=headers, json=tag_payload)
                
                # Always log the response for debugging
                logger.debug(f"Mailchimp tag response status for {email}: {response.status_code}")
                logger.debug(f"Mailchimp tag response body for {email}: {response.text}")
                
                # Log full details on non-success
                if response.status_code not in (200, 204):
                    logger.warning(f"Failed to apply tag to {email}: Status {response.status_code}")
                    logger.warning(f"Response: {response.text}")
                    
                    # Check if the member exists
                    check_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                    check_response = requests.get(check_url, auth=auth, headers=headers)
                    if check_response.status_code == 404:
                        logger.error(f"Member {email} not found in Mailchimp - cannot apply tag")
                    elif check_response.status_code == 200:
                        member_data = check_response.json()
                        logger.info(f"Member exists with status: {member_data.get('status')}")
                        
                    response.raise_for_status()
                else:
                    logger.info(f"Successfully applied tag '{MAILCHIMP_TAG}' to {email}")
                    
                    # Verify tag was actually applied by re-fetching the contact
                    time.sleep(1)  # Brief delay to allow tag processing
                    verify_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                    verify_response = requests.get(verify_url, auth=auth, headers=headers)
                    
                    if verify_response.status_code == 200:
                        member_data = verify_response.json()
                        tags = member_data.get("tags", [])
                        tag_names = [tag.get("name") for tag in tags]
                        
                        if MAILCHIMP_TAG in tag_names:
                            logger.info(f"✅ Verified tag '{MAILCHIMP_TAG}' was successfully applied to {email}")
                        else:
                            logger.warning(f"⚠️ Tag '{MAILCHIMP_TAG}' not found on contact {email} despite successful API response!")
                    
                    return True
                
                break
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for tagging {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise
                    
    except Exception as e:
        logger.error(f"Error applying tag to {email}: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            logger.error(f"Response: {e.response.text}")
        return False
        
    return False


def remove_mailchimp_contact_by_email(email: str) -> bool:
    """
    Remove a contact from Mailchimp audience.
    Returns True if successful, False otherwise.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return False
    
    email = email.lower()
    subscriber_hash = calculate_subscriber_hash(email)
    
    auth = ("anystring", MAILCHIMP_API_KEY)

    # STEP 1: Inactivate the tag on this member
    tags_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
    tag_payload = {"tags": [{"name": MAILCHIMP_TAG, "status": "inactive"}]}
    logger.info(f"Removing tag '{MAILCHIMP_TAG}' from {email}")
    resp = requests.post(tags_url, auth=auth, json=tag_payload)
    if resp.status_code not in (204, 200):
        logger.warning(f"Failed to remove tag from {email}: {resp.text}")

    # STEP 2: Archive the member
    archive_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.delete(archive_url, auth=auth)
                
                if response.status_code in (204, 200):
                    logger.info(f"✅ Archived contact in Mailchimp: {email}")
                    return True
                elif response.status_code == 404:
                    logger.warning(f"Contact already archived/not found: {email}")
                    return True
                else:
                    response.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise
        
    except Exception as e:
        logger.error(f"Error removing Mailchimp contact {email}: {e}")
        return False
    
    return False


def fetch_mailchimp_merge_fields() -> Dict[str, Any]:
    """
    Fetch all merge fields for the Mailchimp audience and validate required fields exist.
    Returns the merge fields data or empty dict if failed.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return {}
    
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/merge-fields"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(f"Fetching Mailchimp merge fields from: {url}")
                response = requests.get(url, auth=auth, headers=headers)
                
                # Log full response on non-200
                if response.status_code != 200:
                    logger.error(f"Failed to fetch Mailchimp merge fields: Status {response.status_code}")
                    logger.error(f"Response: {response.text}")
                
                response.raise_for_status()
                data = response.json()
                
                # Extract all merge field tags
                fields = data.get("merge_fields", [])
                field_tags = [field.get("tag") for field in fields]
                
                # Log all available merge fields
                logger.info(f"Mailchimp merge fields available: {field_tags}")
                
                # Check required fields exist
                required_fields = ["COMPANY", "PHONE", "CITY", "INDUSTRY"]
                missing_fields = [field for field in required_fields if field not in field_tags]
                
                if missing_fields:
                    logger.warning(f"Mailchimp is missing these required merge-fields: {missing_fields}. Creating them now…")
                    # Map tags to human-readable names
                    display_names = {"COMPANY": "Company", "CITY": "City", "INDUSTRY": "Industry"}
                    for tag in missing_fields:
                        create_mailchimp_merge_field(tag, display_names.get(tag, tag.title()))
                    # Re-fetch merge fields to include newly created ones
                    resp = requests.get(
                        f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/merge-fields",
                        auth=("anystring", MAILCHIMP_API_KEY)
                    )
                    resp.raise_for_status()
                    available = [m.get("tag") for m in resp.json().get("merge_fields", [])]
                    logger.info(f"Merge-fields after creation: {available}")
                    missing_after = [f for f in REQUIRED_TAGS if f not in available]
                    if missing_after:
                        logger.error(f"Still missing merge-fields after creation: {missing_after}. Aborting.")
                        sys.exit(1)
                else:
                    logger.info("✅ All required merge fields exist in Mailchimp audience")
                
                # Return the full fields data
                return data
                
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for merge fields: {e}. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise
                    
    except Exception as e:
        logger.error(f"Error fetching Mailchimp merge fields: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
    
    return {}


def get_mailchimp_contact_status(email: str) -> Dict[str, Any]:
    """
    Get the current status of a contact in Mailchimp.
    Returns the full contact data or empty dict if failed.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return {}
    
    email = email.lower()
    subscriber_hash = calculate_subscriber_hash(email)
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(f"Checking Mailchimp contact status for {email}")
                response = requests.get(url, auth=auth, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "unknown")
                    
                    # Check if the status might cause UI visibility issues
                    if status not in ["subscribed", "unsubscribed"]:
                        logger.warning(f"⚠️ Contact {email} has status '{status}' which may not be visible in Mailchimp UI")
                    else:
                        logger.info(f"✅ Verified Mailchimp contact status for {email}: {status}")
                    
                    # Log which tags are applied
                    tags = data.get("tags", [])
                    tag_names = [tag.get("name") for tag in tags]
                    logger.info(f"Contact {email} has tags: {tag_names}")
                    
                    # Check if our tag is applied
                    if MAILCHIMP_TAG not in tag_names:
                        logger.warning(f"⚠️ Tag '{MAILCHIMP_TAG}' not found on contact {email}")
                    
                    # Return the full contact data
                    return data
                elif response.status_code == 404:
                    logger.error(f"❌ Contact {email} not found in Mailchimp despite successful upsert!")
                    return {}
                else:
                    logger.error(f"Failed to check contact status for {email}: Status {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    response.raise_for_status()
                
                break
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for status check {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    raise
                    
    except Exception as e:
        logger.error(f"Error checking Mailchimp contact status for {email}: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
    
    return {}


def fetch_hubspot_list_name(list_id: str) -> str:
    """Retrieve the HubSpot list’s human-readable name for tagging."""
    url = f"https://api.hubapi.com/crm/v3/lists/{list_id}"
    headers = {
        "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json().get("name", f"hubspot_list_{list_id}")

def main():
    """Main execution function."""
    global MAILCHIMP_TAG
    logger.info("Starting HubSpot → Mailchimp sync for lists: %s", HUBSPOT_LIST_IDS)
    logger.info("Configuration: PAGE_SIZE=%d, TEST_CONTACT_LIMIT=%d, MAX_RETRIES=%d, RETRY_DELAY=%d", PAGE_SIZE, TEST_CONTACT_LIMIT, MAX_RETRIES, RETRY_DELAY)
    for list_id in HUBSPOT_LIST_IDS:
        logger.info("%s Syncing list %s %s", "="*10, list_id, "="*10)
        try:
            list_name = fetch_hubspot_list_name(list_id)
            global MAILCHIMP_TAG
            MAILCHIMP_TAG = list_name
            logger.info("Contacts will be tagged with: '%s'", MAILCHIMP_TAG)
        except Exception as e:
            logger.error("Failed to fetch name for list %s: %s", list_id, e)
            continue

        # Validate environment and merge fields
        if not validate_environment():
            logger.error("Environment validation failed for list %s", list_id)
            continue
        logger.info("Validating Mailchimp merge fields...")
        merge_fields = fetch_mailchimp_merge_fields()
        if not merge_fields:
            logger.warning("⚠️ Could not validate Mailchimp merge fields - continuing anyway")

        # Step 1: Fetch contacts
        logger.info("Fetching contacts from HubSpot list %s...", list_id)
        hubspot_contacts = get_hubspot_contacts(list_id)
        if not hubspot_contacts:
            logger.error("No valid contacts found for list %s or failed to fetch", list_id)
            continue

        # Step 2: Create a set of all HubSpot emails for comparison
        hubspot_emails = {contact["email"].lower() for contact in hubspot_contacts}
        logger.info(f"Found {len(hubspot_emails)} unique emails in HubSpot for list {list_id}")

        # Step 3: Upsert all HubSpot contacts to Mailchimp
        successful_upserts = 0
        for contact in hubspot_contacts:
            if upsert_mailchimp_contact(contact):
                successful_upserts += 1
            time.sleep(0.2)
        logger.info(f"Successfully upserted {successful_upserts} contacts to Mailchimp for list {list_id}")

        # Step 4: Get current Mailchimp members with our tag
        mailchimp_emails_dict = get_current_mailchimp_emails()
        mailchimp_emails = set(mailchimp_emails_dict.keys())

        # Step 5: Find and remove stale contacts
        emails_to_remove = mailchimp_emails - hubspot_emails
        if emails_to_remove:
            logger.info(f"Found {len(emails_to_remove)} contacts to remove from Mailchimp for list {list_id}")
            successful_removals = 0
            for email in emails_to_remove:
                if remove_mailchimp_contact_by_email(email):
                    successful_removals += 1
                time.sleep(0.2)
            logger.info(f"Successfully removed {successful_removals} contacts from Mailchimp for list {list_id}")
        else:
            logger.info("No contacts to remove from Mailchimp for list %s", list_id)
        
        logger.info("%s Completed sync for list %s %s", "="*10, list_id, "="*10)

    logger.info("All configured HubSpot lists have been synced.")


if __name__ == "__main__":
    main()

