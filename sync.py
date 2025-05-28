#!/usr/bin/env python
"""
HubSpot → Mailchimp Sync Agent

This script synchronizes contacts from HubSpot lists into a Mailchimp audience,
dynamically fetching list metadata and preserving all other tags.
"""

import os
import sys
import time
import math
from datetime import datetime, timezone
import json
import logging
import hashlib
import requests

# ─── IMPORT CONFIGURATION FROM MAIN ─────────────────────────────────────────
from main import (
    HUBSPOT_LIST_IDS, HUBSPOT_PRIVATE_TOKEN,
    MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC,
    PAGE_SIZE, TEST_CONTACT_LIMIT, MAX_RETRIES, RETRY_DELAY,
    REQUIRED_TAGS, LOG_LEVEL, RAW_DATA_DIR, RUN_MODE
)

# ─── RAW_DATA FOLDER STRUCTURE ─────────────────────────────────────────
RAW_BASE      = RAW_DATA_DIR
METADATA_DIR  = os.path.join(RAW_BASE, "metadata")
SNAPSHOT_DIR  = os.path.join(RAW_BASE, "snapshots")
MEM_DIR       = os.path.join(SNAPSHOT_DIR, "memberships")
CONT_DIR      = os.path.join(SNAPSHOT_DIR, "contacts")
LIST_NAME_MAP = os.path.join(RAW_BASE, "list_name_map.json")
HISTORY_FILE  = os.path.join(RAW_BASE, "list_name_history.json")
# Retain raw data for this many days before pruning:
RETENTION_DAYS = int(os.getenv("RAW_RETENTION_DAYS", "7"))

# Ensure new dirs exist
for d in (METADATA_DIR, MEM_DIR, CONT_DIR):
    os.makedirs(d, exist_ok=True)
from tqdm import tqdm
from typing import Dict, List, Any, Optional, Set

# ── Initialize logging ─────────────────────────────────────────────────────────
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "sync.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)
root_logger = logging.getLogger()
# summary.log for INFO+
summary_handler = logging.FileHandler(os.path.join(LOG_DIR, "summary.log"))
summary_handler.setLevel(logging.INFO)
summary_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
root_logger.addHandler(summary_handler)

# Quiet noisy libs
logging.getLogger("urllib3").setLevel(logging.INFO)
logging.getLogger("requests").setLevel(logging.INFO)

# ── Ensure raw_data directory exists ────────────────────────────────────────────
os.makedirs(RAW_DATA_DIR, exist_ok=True)

# ── Remove any loose root‐level JSON (except map/history) ───────────────────
def remove_loose_root_files():
    for fn in os.listdir(RAW_BASE):
        path = os.path.join(RAW_BASE, fn)
        if fn.endswith(".json") and fn not in (
            os.path.basename(LIST_NAME_MAP),
            os.path.basename(HISTORY_FILE)
        ):
            os.remove(path)
            logger.debug(f"Removed loose root JSON: {path}")

remove_loose_root_files()

# Global error flag for CI failure detection
had_errors = False

# ─── Prune old raw + snapshot files ────────────────────────────────────────
def prune_old_files():
    cutoff = time.time() - RETENTION_DAYS * 86400
    for root, dirs, files in os.walk(RAW_BASE):
        # never prune map/history at top level
        if root == RAW_BASE:
            continue
        for fn in files:
            if fn.endswith(".json"):
                path = os.path.join(root, fn)
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    logger.debug(f"Pruned old file: {path}")

prune_old_files()

def record_list_name_history(list_id: str, list_name: str) -> None:
    """
    Record list name changes in the history file.
    This function tracks when a list name changes and maintains a permanent history.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    history_exists = os.path.exists(HISTORY_FILE)
    
    # Load existing history file or create new one
    if history_exists:
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except json.JSONDecodeError:
            # Corrupted history file, start fresh
            logger.warning(f"History file {HISTORY_FILE} corrupted, creating new history")
            history = {}
    else:
        history = {}
    
    # Initialize list history if not present
    if list_id not in history:
        history[list_id] = []
    
    # Check if this is a new name or the first record for this list
    list_history = history[list_id]
    if not list_history or list_history[-1].get('name') != list_name:
        # Record the change with timestamp
        list_history.append({
            'name': list_name,
            'timestamp': timestamp
        })
        
        # Write updated history back to file
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
        
        logger.debug(f"Recorded name '{list_name}' for list {list_id} in history")

# -----------------------------------------------------------------------------


# Path to persist mapping of list IDs and raw metadata is
# defined above as LIST_NAME_MAP

def fetch_and_dump_list_metadata(list_id: str) -> dict:
    """
    Fetch HubSpot list metadata (v3→v1), dump JSON into timestamped metadata directory,
    and return the parsed body.
    """
    # Create timestamp-based directory for metadata
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    metadata_date_dir = os.path.join(METADATA_DIR, timestamp)
    os.makedirs(metadata_date_dir, exist_ok=True)
    
    headers = {
        "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
        "Content-Type": "application/json",
    }

    # 1) v3 Active Lists API
    v3_url = f"https://api.hubapi.com/crm/v3/lists/{list_id}"
    try:
        resp = requests.get(v3_url, headers=headers)
        if resp.status_code == 200:
            body = resp.json()
            # Save in timestamped directory
            path = os.path.join(metadata_date_dir, f"hubspot_list_{list_id}_metadata.json")
            with open(path, "w") as f:
                json.dump(body, f, indent=2)
            # (legacy root‐level dump removed)
            logger.info(f"Wrote v3 list metadata to {path}")
            return body
        elif resp.status_code != 404:
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Error fetching v3 metadata for list {list_id}: {e}")

    # 2) v1 Static Lists API
    v1_url = f"https://api.hubapi.com/contacts/v1/lists/{list_id}"
    try:
        resp = requests.get(v1_url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        # Save in timestamped directory
        path = os.path.join(metadata_date_dir, f"hubspot_list_{list_id}_static_metadata.json")
        with open(path, "w") as f:
            json.dump(body, f, indent=2)
        # (legacy root‐level dump removed)
        logger.info(f"Wrote v1 static metadata to {path}")
        return body
    except Exception as e:
        logger.warning(f"Error fetching v1 metadata for list {list_id}: {e}")

    logger.error(f"Failed to fetch metadata for list {list_id}")
    return {}



# To see debug messages (e.g. “Parsed v3 list name…”), run with LOG_LEVEL=DEBUG

## Pull configuration from py
HUBSPOT_PRIVATE_TOKEN  = HUBSPOT_PRIVATE_TOKEN
# list of list IDs to sync
HUBSPOT_LIST_IDS       = HUBSPOT_LIST_IDS
# Mailchimp settings
MAILCHIMP_API_KEY      = MAILCHIMP_API_KEY
MAILCHIMP_LIST_ID      = MAILCHIMP_LIST_ID
MAILCHIMP_DC           = MAILCHIMP_DC
# Mailchimp base URL
MAILCHIMP_BASE_URL     = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"

# paging, limits & retries from config
PAGE_SIZE              = PAGE_SIZE
TEST_CONTACT_LIMIT     = TEST_CONTACT_LIMIT
MAX_RETRIES            = MAX_RETRIES
RETRY_DELAY            = RETRY_DELAY

# merge-fields to enforce
REQUIRED_TAGS          = REQUIRED_TAGS

# Helper: Create a new merge-field on the Mailchimp audience
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

# runtime variables
MAILCHIMP_TAG = None
# Removed per-module LOG_LEVEL override to retain DEBUG on sync.log


def validate_environment() -> bool:
    """Validate that all required configuration values are set in py."""
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
    
    # Create dated contacts directory for snapshot
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    contacts_date_dir = os.path.join(CONT_DIR, current_date)
    os.makedirs(contacts_date_dir, exist_ok=True)
    
    # Prepare contacts snapshot file
    contacts_file = os.path.join(contacts_date_dir, f"hubspot_list_{list_id}_contacts.json")
    with open(contacts_file, "w") as f:
        f.write(f"# HubSpot Raw Contacts from List ID {list_id} - Generated on {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    
    # (legacy root‐level raw_contacts.json dump removed)
    
    headers = {
        "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Define the properties we want to retrieve from the Batch Read API
    properties = [
        "email",
        "company",
        "phone",
        "address",        # street address
        "address2",       # second address line
        "city",
        "state_region",   # or your portal’s internal name for state
        "postal_code",    # postal code
        "country_region", # or “country”
        "branches",       # branch assignments
        "firstname",
        "lastname"
    ]
    
    # STEP 1 (v3): Fetch contact IDs via CRM v3 Lists API
    logger.info(f"STEP 1 (v3): Fetching contact IDs from list {list_id}")  # Major step indicator
    list_url = f"https://api.hubapi.com/crm/v3/lists/{list_id}/memberships"
    # Page size from config
    params = {"limit": PAGE_SIZE}
    after = None
    all_vids: List[str] = []
    page = 1

    expected_pages = None
    while True:
        if after:
            params["after"] = after

        logger.debug(f"Fetching CRM v3 memberships page {page}")  # Detailed pagination info
        resp = requests.get(list_url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        # On first page, log total and expected page count
        if page == 1:
            total_members = body.get("total")
            if isinstance(total_members, int):
                expected_pages = math.ceil(total_members / params.get("limit", 1))
                logger.debug(f"Total memberships in list: {total_members}, expecting ~{expected_pages} pages at {params['limit']} per page")

        # Create dated memberships directory for snapshot
        membership_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        membership_dir = os.path.join(MEM_DIR, membership_date)
        os.makedirs(membership_dir, exist_ok=True)
        
        # Save to snapshot directory
        snapshot_path = os.path.join(membership_dir, f"hubspot_list_{list_id}_memberships_page_{page}.json")
        with open(snapshot_path, "w") as f:
            json.dump(body, f, indent=2)

        # Each membership has recordId = contact’s internal ID
        results = body.get("results", [])
        vids = [m.get("recordId") for m in results if m.get("recordId")]
        logger.debug(f"Retrieved {len(vids)} IDs from page {page}")  # IDs fetched
        all_vids.extend(vids)
        # Early exit if test cap reached
        if TEST_CONTACT_LIMIT > 0 and len(all_vids) >= TEST_CONTACT_LIMIT:
            logger.debug(f"TEST_CONTACT_LIMIT={TEST_CONTACT_LIMIT} reached; ending STEP 1 early")
            all_vids = all_vids[:TEST_CONTACT_LIMIT]
            break
        # Pagination cursor
        paging = body.get("paging", {}).get("next", {})
        after = paging.get("after")
        if not after:
            logger.debug("No further paging cursor returned – ending STEP 1")
            break

        page += 1
        time.sleep(1)  # respect rate limits

    logger.info(f"STEP 1 COMPLETE: collected {len(all_vids)} contact IDs")
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
        logger.debug(f"Processing batch {batch_counter} of {len(vid_batches)} with {len(vid_batch)} contacts")
        
        # Create batch read payload
        batch_payload = {
            "properties": properties,
            "inputs": [{"id": str(vid)} for vid in vid_batch]
        }
        
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    # Send batch request
                    logger.debug(f"Sending Batch Read API request for {len(vid_batch)} contacts")
                    logger.debug(f"Batch Read payload: {json.dumps(batch_payload)}")
                    
                    response = requests.post(batch_url, headers=headers, json=batch_payload)
                    
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])
                        
                        # Log success info
                        logger.debug(f"Successfully retrieved {len(results)} contact details from batch {batch_counter}")
                        
                            # Write raw contact data to snapshot file
                        if results:
                            # Write to snapshot file
                            with open(contacts_file, "a") as f:
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
                                    "email":     email.lower(),  # Ensure email is lowercase
                                    "company":   (contact_properties.get("company") or "")[:255],
                                    "phone":     (contact_properties.get("phone") or "")[:50],
                                    "address":   (contact_properties.get("address") or "")[:255],
                                    "address2":  (contact_properties.get("address2") or "")[:255],
                                    "city":      (contact_properties.get("city") or "")[:50],
                                    "state":     (contact_properties.get("state_region") or "")[:50],
                                    "postcode":  (contact_properties.get("postal_code") or "")[:20],
                                    "country":   (contact_properties.get("country_region") or "")[:50],
                                    "branches":  (contact_properties.get("branches") or "")[:255],
                                    "firstname": (contact_properties.get("firstname") or "")[:50],
                                    "lastname":  (contact_properties.get("lastname") or "")[:50]
                                }
                                
                                # Extract any additional useful contact info that might be available
                                contact_id = contact.get("id")
                                if contact_id:
                                    contact_data["hubspot_id"] = str(contact_id)
                                
                                # If we made it here, we have a valid contact with at least an email
                                batch_contacts.append(contact_data)
                            
                            contacts.extend(batch_contacts)
                            logger.debug(f"Processed {len(batch_contacts)} valid contacts from batch {batch_counter}")
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


"""
# Removed unused helper: get_contact_property
"""


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

def get_all_mailchimp_emails() -> Set[str]:
    """
    Fetch all members' emails from the Mailchimp audience (no tag filter).
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return set()
    emails = set()
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members"
    params = {"count": 1000, "offset": 0}
    try:
        has_more = True
        while has_more:
            response = requests.get(url, auth=auth, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            for m in data.get("members", []):
                email = m.get("email_address", "").lower()
                if email:
                    emails.add(email)
            total = data.get("total_items", 0)
            current = params['offset'] + len(data.get("members", []))
            if current < total:
                params['offset'] = current
            else:
                has_more = False
            if has_more:
                time.sleep(0.5)
    except Exception as e:
        logger.error(f"Error fetching all Mailchimp members: {e}")
    logger.info(f"Found {len(emails)} total Mailchimp audience members")
    return emails


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
        "COMPANY":  contact.get("company", "")[:255],
        "PHONE":    contact.get("phone", "")[:50],
        "ADDRESS":  contact.get("address", "")[:255],
        "ADDRESS2": contact.get("address2", "")[:255],
        "CITY":     contact.get("city", "")[:50],
        "STATE":    contact.get("state", "")[:50],
        "POSTCODE": contact.get("postcode", "")[:20],
        "COUNTRY":  contact.get("country", "")[:50],
        "BRANCHES": contact.get("branches", "")[:255]
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
                        logger.debug(f"Contact {email} status after upsert: {response_body['status']}")
                        if response_body["status"] not in ["subscribed", "unsubscribed"]:
                            logger.warning(f"⚠️ Contact {email} has status '{response_body['status']}' which may not be visible in Mailchimp UI")
                except ValueError:
                    logger.debug(f"No JSON response body for {email}")
                
                # Handle successful responses
                if response.status_code in (200, 201):
                    logger.debug(f"Successfully upserted contact: {email} (Status: {response.status_code})")
                    
                    # Apply tag with increased delay to ensure the member is fully created/updated
                    logger.debug(f"Waiting 2 seconds before applying tag to {email}")
                    time.sleep(2)  # Increased from 1s to 2s
                    if apply_mailchimp_tag(email):
                        logger.debug(f"Successfully tagged {email} with '{MAILCHIMP_TAG}'")
                        
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
                
                break  # Break out of retry loop on successful completion
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
                        logger.debug(f"Member exists with status: {member_data.get('status')}")
                        
                    response.raise_for_status()
                else:
                    logger.debug(f"Successfully applied tag '{MAILCHIMP_TAG}' to {email}")
                    
                    # Verify tag was actually applied by re-fetching the contact
                    time.sleep(1)  # Brief delay to allow tag processing
                    verify_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                    verify_response = requests.get(verify_url, auth=auth, headers=headers)
                    
                    if verify_response.status_code == 200:
                        member_data = verify_response.json()
                        tags = member_data.get("tags", [])
                        tag_names = [tag.get("name") for tag in tags]
                        
                        if MAILCHIMP_TAG in tag_names:
                            logger.debug(f"✅ Verified tag '{MAILCHIMP_TAG}' was successfully applied to {email}")
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

    # Archive the member (DELETE) in Mailchimp
    archive_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    
    try:
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.delete(archive_url, auth=auth)
                
                if response.status_code in (204, 200):
                    logger.debug(f"✅ Archived contact in Mailchimp: {email}")
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
 
 
def untag_mailchimp_contact(email: str) -> bool:
    """
    Inactivate the current MAILCHIMP_TAG on a Mailchimp contact without archiving them.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set")
        return False
    email = email.lower()
    subscriber_hash = calculate_subscriber_hash(email)
    url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    payload = {"tags": [{"name": MAILCHIMP_TAG, "status": "inactive"}]}
    try:
        for attempt in range(MAX_RETRIES):
            response = requests.post(url, auth=auth, headers=headers, json=payload)
            if response.status_code in (200, 204):
                logger.debug(f"✅ Removed tag '{MAILCHIMP_TAG}' from {email}")
                return True
            else:
                logger.warning(f"Failed to remove tag '{MAILCHIMP_TAG}' from {email}: {response.text}")
                response.raise_for_status()
    except Exception as e:
        logger.error(f"Error untagging contact {email}: {e}")
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
                response = requests.get(
                    url,
                    auth=auth,
                    headers=headers,
                    params={"count": 1000}
                )
                
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
                
                # Check required fields exist (use config-defined REQUIRED_TAGS)
                missing_fields = [field for field in REQUIRED_TAGS if field not in field_tags]
                
                if missing_fields:
                    logger.warning(f"Mailchimp is missing these required merge-fields: {missing_fields}. Creating them now…")
                    # Map tags to human-readable names
                    display_names = {
                        "COMPANY":  "Company",
                        "PHONE":    "Phone",
                        "ADDRESS":  "Address",
                        "ADDRESS2": "Address 2",
                        "CITY":     "City",
                        "STATE":    "State/Region",
                        "POSTCODE": "Postal Code",
                        "COUNTRY":  "Country/Region",
                        "BRANCHES": "Branches",
                        "FNAME":    "First Name",
                        "LNAME":    "Last Name"
                    }
                    for tag in missing_fields:
                        create_mailchimp_merge_field(tag, display_names.get(tag, tag.title()))
                    # Re-fetch merge fields to include newly created ones
                    resp = requests.get(
                        f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/merge-fields",
                        auth=("anystring", MAILCHIMP_API_KEY),
                        params={"count": 1000}
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
                        logger.debug(f"✅ Verified Mailchimp contact status for {email}: {status}")

                    # Log which tags are applied
                    tags = data.get("tags", [])
                    tag_names = [tag.get("name") for tag in tags]
                    logger.debug(f"Contact {email} has tags: {tag_names}")
                    
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
    """
    Extract the human-readable list name from the freshly dumped metadata.
    """
    # 1) Fetch & dump metadata from API
    meta = fetch_and_dump_list_metadata(list_id)

    # 2) v3 response nests data under 'list'
    list_obj = meta.get("list") or {}
    if isinstance(list_obj, dict):
        name = list_obj.get("name") or list_obj.get("displayName")
        if name:
            logger.debug(f"Parsed v3 list name for {list_id}: '{name}'")
            return name

    # 3) v1 static metadata returns at root
    name = meta.get("name")
    if name:
        logger.debug(f"Parsed static list name for {list_id}: '{name}'")
        return name

    # 4) Final fallback to ID‐based tag
    fallback = f"hubspot_list_{list_id}"
    logger.warning(f"No valid name in metadata for list {list_id}; falling back to '{fallback}'")
    return fallback

def rename_mailchimp_tag_definition(old_name: str, new_name: str) -> bool:
    """
    Rename a Mailchimp tag by exploiting Mailchimp's internal architecture.
    
    This function leverages the fact that Mailchimp tags are implemented as static 
    segments internally. By using the segments API to rename the underlying segment,
    the tag name is updated across all members automatically with zero member 
    operations required. This achieves true in-place renaming.
    
    Technical approach:
    1. Search for tag using /tag-search endpoint  
    2. Rename via /segments/{tag_id} endpoint (tags are static segments)
    3. Verify rename success
    
    Returns True if successful, False otherwise.
    """
    if not all([MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC]):
        logger.error("Mailchimp credentials not properly set for tag rename")
        return False
    
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}
    
    logger.info(f"🏷️  DIRECT TAG RENAME VIA MAILCHIMP API: '{old_name}' → '{new_name}'")
    
    # Step 1: Get the tag ID of the old tag
    tag_search_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/tag-search"
    tag_search_params = {"name": old_name}
    
    try:
        tag_id = None
        
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(f"Searching for tag: '{old_name}'")
                response = requests.get(tag_search_url, auth=auth, headers=headers, params=tag_search_params)
                response.raise_for_status()
                
                tag_data = response.json()
                tags = tag_data.get("tags", [])
                
                for tag in tags:
                    if tag.get("name") == old_name:
                        tag_id = tag.get("id")
                        break
                
                if tag_id:
                    logger.debug(f"Found tag ID for '{old_name}': {tag_id}")
                    break
                else:
                    logger.warning(f"Tag '{old_name}' not found in Mailchimp")
                    return False
                
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"API request failed when searching for tag: {e}. Retrying...")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Failed to find tag '{old_name}': {e}")
                    return False
        
        # Step 2: Update the tag name
        if not tag_id:
            logger.error(f"Could not find tag ID for '{old_name}'")
            return False
        
        tag_update_url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/segments/{tag_id}"
        tag_update_payload = {"name": new_name}
        
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(f"Updating tag name from '{old_name}' to '{new_name}'")
                response = requests.patch(tag_update_url, auth=auth, headers=headers, json=tag_update_payload)
                
                if response.status_code == 200:
                    logger.info(f"✅ Successfully renamed tag '{old_name}' to '{new_name}'")
                    
                    # Verify the tag was renamed correctly
                    updated_tag = response.json()
                    if updated_tag.get("name") == new_name:
                        logger.info(f"✅ Verified tag rename success, new name: '{updated_tag.get('name')}'")
                    else:
                        logger.warning(f"⚠️ Tag was renamed but returned unexpected name: '{updated_tag.get('name')}'")
                    
                    return True
                else:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(f"⚠️ Failed to rename tag (Status: {response.status_code}). Retrying...")
                        time.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"❌ Failed to rename tag: {response.status_code}")
                        logger.error(f"Response: {response.text}")
                        return False
                    
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"⚠️ Error during tag rename: {e}. Retrying...")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"❌ Error renaming tag: {e}")
                    return False
                    
    except Exception as e:
        logger.error(f"❌ Unexpected error during tag rename: {e}")
        return False
        
    return False


def _get_members_with_tag(tag_name: str, auth: tuple, headers: dict) -> list:
    """Get all members who have a specific tag."""
    logger.debug(f"Finding all members with tag '{tag_name}'...")
    
    url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members"
    members_with_tag = []
    params = {"count": 1000, "offset": 0}
    
    try:
        while True:
            response = requests.get(url, auth=auth, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            members = data.get("members", [])
            if not members:
                break
            
            for member in members:
                member_tags = [tag.get("name") for tag in member.get("tags", [])]
                if tag_name in member_tags:
                    members_with_tag.append({
                        "email": member.get("email_address"),
                        "subscriber_hash": member.get("id"),
                        "current_tags": member_tags
                    })
            
            # Check pagination
            total_items = data.get("total_items", 0)
            current_count = params['offset'] + len(members)
            
            if current_count < total_items:
                params['offset'] = current_count
            else:
                break
                
    except Exception as e:
        logger.error(f"❌ Error fetching members with tag '{tag_name}': {e}")
        return []
    
    logger.debug(f"Found {len(members_with_tag)} members with tag '{tag_name}'")
    return members_with_tag

def main():
    """Main execution function."""

    global MAILCHIMP_TAG, had_errors
    had_errors = False
    # ─── Load previous list names from disk ───
    if os.path.exists(LIST_NAME_MAP):
        with open(LIST_NAME_MAP, "r") as f:
            previous_list_names = json.load(f)
    else:
        previous_list_names = {}
    # Track every synced email across all lists for final archival cleanup
    all_synced_emails: Set[str] = set()
    logger.info("Starting multi-list HubSpot → Mailchimp sync for lists: %s", HUBSPOT_LIST_IDS)
    logger.info("Configuration: PAGE_SIZE=%d, TEST_CONTACT_LIMIT=%d, MAX_RETRIES=%d, RETRY_DELAY=%d", PAGE_SIZE, TEST_CONTACT_LIMIT, MAX_RETRIES, RETRY_DELAY)
    for list_id in HUBSPOT_LIST_IDS:
        logger.info("%s Syncing list %s %s", "="*10, list_id, "="*10)
        try:
            # ─── Fetch current HubSpot list name ───
            list_name = fetch_hubspot_list_name(list_id)

            # ─── If the list was renamed, remove ONLY the old list‐specific tag ───
            old_name = previous_list_names.get(list_id)
            if old_name and old_name != list_name:
                logger.info(
                    f"HubSpot list {list_id} renamed from '{old_name}' to '{list_name}'. "
                    "Renaming Mailchimp tag in place…"
                )
                # Rename the segment definition on Mailchimp—no mass untagging or deletion
                rename_success = rename_mailchimp_tag_definition(old_name, list_name)
                
                # If rename failed, implement fallback - untag Mailchimp members who still have the old tag
                if not rename_success:
                    logger.warning(
                        f"Failed to rename tag '{old_name}' to '{list_name}'. "
                        "Implementing fallback: untagging Mailchimp members with old tag."
                    )
                    
                    # Temporarily switch to the old tag so we can find exactly those members
                    prev_tag = MAILCHIMP_TAG
                    MAILCHIMP_TAG = old_name
                    # Fetch Mailchimp members still carrying the old tag
                    old_members = list(get_current_mailchimp_emails().keys())
                    if old_members:
                        for email in old_members:
                            untag_mailchimp_contact(email)
                        logger.info(f"Successfully removed old tag '{old_name}' from {len(old_members)} contacts")
                    else:
                        logger.info(f"No Mailchimp members found with tag '{old_name}'")
                    # Restore the previous tag so the rest of the sync uses the correct new tag
                    MAILCHIMP_TAG = prev_tag
                else:
                    logger.info(f"Successfully renamed tag '{old_name}' to '{list_name}'!")


            # ─── Record history and persist the new name for next run ───
            previous_list_names[list_id] = list_name
            with open(LIST_NAME_MAP, "w") as f:
                json.dump(previous_list_names, f, indent=2)
                
            # Record the list name in history
            record_list_name_history(list_id, list_name)

            # ─── Now use the up-to-date tag for the normal sync flow ───
            MAILCHIMP_TAG = list_name

            logger.info("Contacts will be tagged with: '%s'", MAILCHIMP_TAG)
        except Exception as e:
            logger.exception("Failed to determine list name for %s: %s", list_id, e)
            had_errors = True
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
        # Track these emails globally for final archival cleanup
        all_synced_emails.update(hubspot_emails)

        # Step 3: Upsert all HubSpot contacts to Mailchimp
        successful_upserts = 0
        # Upsert contacts with progress bar
        for contact in tqdm(hubspot_contacts,
                            desc=f"Upserting contacts for list {list_id}",
                            unit="contact"):
            if upsert_mailchimp_contact(contact):
                successful_upserts += 1
            time.sleep(0.2)
        logger.info(f"Successfully upserted {successful_upserts} contacts to Mailchimp for list {list_id}")

        # Step 4: Get current Mailchimp members with our tag
        mailchimp_emails_dict = get_current_mailchimp_emails()
        mailchimp_emails = set(mailchimp_emails_dict.keys())

        # Step 5: Find and untag stale contacts
        emails_to_untag = mailchimp_emails - hubspot_emails
        if emails_to_untag:
            logger.info(f"Found {len(emails_to_untag)} contacts to untag from Mailchimp for list {list_id}")
            successful_untags = 0
            # Untag contacts with progress bar
            for email in tqdm(emails_to_untag,
                               desc=f"Untagging stale contacts for list {list_id}",
                               unit="contact"):
                if untag_mailchimp_contact(email):
                    successful_untags += 1
                time.sleep(0.2)
            logger.info(f"Successfully removed tag from {successful_untags} contacts for list {list_id}")
        else:
            logger.info("No contacts to untag from Mailchimp for list %s", list_id)
        
        logger.info("%s Completed sync for list %s %s", "="*10, list_id, "="*10)

    # --- Phase 3: Global cleanup (archive any Mailchimp members not in any synced list) ---
    logger.info("Starting global archival cleanup: members not in any HubSpot list will be archived")
    all_mc_emails = get_all_mailchimp_emails()
    to_archive = all_mc_emails - all_synced_emails
    if to_archive:
        logger.info(f"Found {len(to_archive)} contacts to archive (no longer in any HubSpot list)")
        archived_count = 0
        # Archive contacts with progress bar
        for email in tqdm(to_archive,
                           desc="Archiving global stale contacts",
                           unit="contact"):
            if remove_mailchimp_contact_by_email(email):
                archived_count += 1
            time.sleep(0.2)
        logger.info(f"Successfully archived {archived_count} contacts from Mailchimp")
    else:
        logger.info("No Mailchimp contacts to archive; all members are in at least one HubSpot list")
    # Final summary

    # If any list failed, abort with non-zero exit
    if had_errors:
        logger.critical("Sync finished with errors—failing the process.")
        sys.exit(1)

    logger.info("Multi-list sync complete: %d unique contacts synced, %d contacts archived", len(all_synced_emails), len(to_archive))
    logger.info("All configured HubSpot lists have been synced and cleanup is complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Unhandled exception—failing CI.")
        sys.exit(1)

