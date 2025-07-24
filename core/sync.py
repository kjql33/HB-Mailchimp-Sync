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
from collections import defaultdict

# ─── IMPORT CONFIGURATION FROM CONFIG ─────────────────────────────────────────
from .config import (
    HUBSPOT_LIST_IDS, HUBSPOT_PRIVATE_TOKEN,
    MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC,
    PAGE_SIZE, TEST_CONTACT_LIMIT, MAX_RETRIES, RETRY_DELAY,
    REQUIRED_TAGS, LOG_LEVEL, RAW_DATA_DIR, RUN_MODE, TEAMS_WEBHOOK_URL,
    HARD_EXCLUDE_LISTS, ENABLE_MAILCHIMP_ARCHIVAL, MUTE_METADATA_FETCH_ERRORS
)

# ─── IMPORT SOURCE LIST TRACKING CONFIGURATION ─────────────────────────────
from .config import ORI_LISTS_FIELD

# ─── PERFORMANCE CONFIGURATION ─────────────────────────────────────────────
from .config import PerformanceConfig
perf_config = PerformanceConfig()

# ─── TEAMS NOTIFICATION SYSTEM ─────────────────────────────────────────────
from .notifications import (
    initialize_notifier, notify_warning, notify_error, notify_info, 
    send_final_notification, get_notifier
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

# ── Initialize Teams notification system ────────────────────────────────────
try:
    if TEAMS_WEBHOOK_URL:
        initialize_notifier(TEAMS_WEBHOOK_URL)
        logger.info("📨 Teams notification system initialized")
    else:
        logger.warning("⚠️ No Teams webhook URL configured - notifications disabled")
except Exception as e:
    logger.warning(f"⚠️ Failed to initialize Teams notifications: {e}")

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
            logger.debug(f"Wrote v3 list metadata to {path}")  # Reduced to DEBUG to minimize log noise
            return body
        elif resp.status_code != 404:
            logger.error(f"HubSpot list access failed: Status {resp.status_code}")
            notify_error("HubSpot list access failed - possible permissions issue",
                       {"list_id": list_id, "status_code": resp.status_code, "api_version": "v3"})
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Error fetching v3 metadata for list {list_id}: {e}")
        if not MUTE_METADATA_FETCH_ERRORS:
            notify_warning("HubSpot v3 list metadata fetch failed",
                         {"list_id": list_id, "error": str(e), "api_version": "v3"})

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
        if not MUTE_METADATA_FETCH_ERRORS:
            notify_warning("HubSpot v1 list metadata fetch failed",
                         {"list_id": list_id, "error": str(e), "api_version": "v1"})

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
        notify_error("API authentication failed - missing required configuration", 
                    {"missing_values": missing})
        return False
    return True


def get_hard_exclude_contact_ids() -> Set[str]:
    """
    Fetch all contact IDs from hard exclude lists.
    These contacts will never be synced to Mailchimp, regardless of which source list they're in.
    """
    if not HARD_EXCLUDE_LISTS:
        logger.debug("No hard exclude lists configured")
        return set()
    
    logger.info(f"🚫 HARD EXCLUDE: Fetching contacts from {len(HARD_EXCLUDE_LISTS)} exclude lists")
    exclude_contact_ids = set()
    
    headers = {
        "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
        "Content-Type": "application/json"
    }
    
    for exclude_list_id in HARD_EXCLUDE_LISTS:
        logger.debug(f"Fetching exclude contacts from list {exclude_list_id}")
        
        # Use same method as regular contact fetching - get membership IDs
        list_url = f"https://api.hubapi.com/crm/v3/lists/{exclude_list_id}/memberships"
        params = {"limit": PAGE_SIZE}
        after = None
        
        try:
            while True:
                if after:
                    params["after"] = after
                
                resp = requests.get(list_url, headers=headers, params=params)
                resp.raise_for_status()
                body = resp.json()
                
                # Extract contact IDs from memberships
                results = body.get("results", [])
                contact_ids = [m.get("recordId") for m in results if m.get("recordId")]
                exclude_contact_ids.update(contact_ids)
                
                # Check for more pages
                paging = body.get("paging", {}).get("next", {})
                after = paging.get("after")
                if not after:
                    break
                    
                time.sleep(perf_config.hubspot_page_delay)
                
        except Exception as e:
            logger.error(f"Failed to fetch contacts from exclude list {exclude_list_id}: {e}")
            continue
    
    logger.info(f"🚫 HARD EXCLUDE: Found {len(exclude_contact_ids)} contacts to exclude from sync")
    return exclude_contact_ids


def filter_excluded_contacts(contacts: List[Dict[str, Any]], exclude_contact_ids: Set[str]) -> List[Dict[str, Any]]:
    """
    Filter out contacts that are in the hard exclude lists.
    """
    if not exclude_contact_ids:
        return contacts
    
    original_count = len(contacts)
    filtered_contacts = []
    excluded_count = 0
    
    for contact in contacts:
        contact_id = contact.get("hubspot_id")  # Use hubspot_id field from processed contact data
        if contact_id and str(contact_id) in exclude_contact_ids:
            excluded_count += 1
            logger.debug(f"🚫 EXCLUDED: Contact {contact_id} ({contact.get('email', 'no-email')}) found in hard exclude list")
        else:
            filtered_contacts.append(contact)
    
    if excluded_count > 0:
        logger.info(f"🚫 HARD EXCLUDE: Filtered out {excluded_count} contacts from sync ({original_count} → {len(filtered_contacts)})")
    
    return filtered_contacts


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
        
        # ─── Transient‑safe wrapper ───────────────────────────
        for attempt in range(MAX_RETRIES):
            resp = requests.get(list_url, headers=headers, params=params)
            try:
                resp.raise_for_status()
                break  # success
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"HubSpot API request failed on list {list_id}, page {page}: {e}. "
                        f"Retrying in {RETRY_DELAY}s (attempt {attempt+1}/{MAX_RETRIES})"
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    # Exhausted retries → critical failure
                    logger.error(f"Exhausted retries fetching list {list_id}: {e}")
                    raise  # halt the sync
        # ───────────────────────────────────────────────────────
        
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
        time.sleep(perf_config.hubspot_page_delay)  # respect rate limits with configurable delay

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
                                    logger.info(f"ℹ️ Skipping contact with missing email: ID {contact.get('id')} (this is expected)")
                                    # Log all missing properties for debugging
                                    missing_fields = [field for field in properties if field not in contact_properties or not contact_properties.get(field)]
                                    if missing_fields:
                                        logger.debug(f"Contact {contact.get('id')} is missing fields: {', '.join(missing_fields)}")
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
                        notify_warning("HubSpot API retry due to network issue",
                                     {"error": str(e), "attempt": attempt + 1, "max_retries": MAX_RETRIES,
                                      "batch": batch_counter, "retry_delay": RETRY_DELAY})
                        time.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"Error fetching contact details batch {batch_counter}: {e}")
                        notify_error("HubSpot API failed after max retries",
                                   {"error": str(e), "batch": batch_counter, "max_retries": MAX_RETRIES})
                        # Continue to the next batch rather than aborting everything
            
            # Increment batch counter
            batch_counter += 1
            
            # Respect API rate limits with configurable delay
            time.sleep(perf_config.hubspot_page_delay)  # Configurable delay between batches to avoid rate limiting
            
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
    
    # 🚫 APPLY HARD EXCLUDE FILTER - Remove contacts in exclude lists
    if HARD_EXCLUDE_LISTS:
        logger.info(f"🚫 HARD EXCLUDE: Checking {len(contacts)} contacts against exclude lists")
        exclude_contact_ids = get_hard_exclude_contact_ids()
        contacts = filter_excluded_contacts(contacts, exclude_contact_ids)
        logger.info(f"🚫 HARD EXCLUDE: Final contact count after filtering: {len(contacts)}")
    
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


def upsert_mailchimp_contact(contact: Dict[str, str], source_list_id: str = None) -> bool:
    """
    Add or update a contact in Mailchimp audience with source list tracking.
    Returns True if successful, False otherwise.
    
    Args:
        contact: Contact data dictionary
        source_list_id: The HubSpot list ID this contact came from (for source tracking)
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
    
    # 🔍 SOURCE LIST TRACKING - Record the original HubSpot list ID
    if source_list_id:
        merge_fields[ORI_LISTS_FIELD] = source_list_id
        logger.debug(f"Recording source list {source_list_id} for contact {email}")
    else:
        logger.warning(f"No source list ID provided for contact {email} - source tracking disabled")
    
    # Check for data truncation and notify
    truncated_fields = []
    for field_name, (field_value, max_length) in [
        ("COMPANY", (contact.get("company", ""), 255)),
        ("PHONE", (contact.get("phone", ""), 50)),
        ("ADDRESS", (contact.get("address", ""), 255)),
        ("ADDRESS2", (contact.get("address2", ""), 255)),
        ("CITY", (contact.get("city", ""), 50)),
        ("STATE", (contact.get("state", ""), 50)),
        ("POSTCODE", (contact.get("postcode", ""), 20)),
        ("COUNTRY", (contact.get("country", ""), 50)),
        ("BRANCHES", (contact.get("branches", ""), 255))
    ]:
        if len(str(field_value)) > max_length:
            truncated_fields.append({
                "field": field_name,
                "original_length": len(str(field_value)),
                "max_length": max_length,
                "truncated_value": str(field_value)[:max_length]
            })
    
    if truncated_fields:
        notify_warning("Contact data truncated to fit Mailchimp field limits",
                     {"email": email, "truncated_fields": truncated_fields})
    
    # Add FNAME and LNAME if available
    if contact.get("firstname"):
        fname_value = str(contact.get("firstname"))
        merge_fields["FNAME"] = fname_value[:50]
        if len(fname_value) > 50:
            notify_warning("First name truncated to fit Mailchimp field limits",
                         {"email": email, "field": "FNAME", "original_length": len(fname_value),
                          "max_length": 50, "truncated_value": fname_value[:50]})
    
    if contact.get("lastname"):
        lname_value = str(contact.get("lastname"))
        merge_fields["LNAME"] = lname_value[:50]
        if len(lname_value) > 50:
            notify_warning("Last name truncated to fit Mailchimp field limits",
                         {"email": email, "field": "LNAME", "original_length": len(lname_value),
                          "max_length": 50, "truncated_value": lname_value[:50]})
        
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
                # ── 1) Pre-flight GET to check if they were archived before upsert
                member_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                pre = requests.get(member_url,
                                   auth=("anystring", MAILCHIMP_API_KEY),
                                   headers={"Content-Type": "application/json"})
                was_archived = False
                if pre.status_code == 200:
                    was_archived = (pre.json().get("status") == "archived")
                    logger.debug(f"Pre-upsert status for {email}: {pre.json().get('status')} (was_archived={was_archived})")
                elif pre.status_code == 404:
                    logger.debug(f"Pre-upsert: {email} not found (new contact)")
                else:
                    logger.debug(f"Pre-upsert check failed for {email}: {pre.status_code}")

                # ── 2) Now do the PUT upsert as before
                response = requests.put(url, auth=auth, headers=headers, json=data)
                
                # Always log full response for debugging
                try:
                    response_body = response.json()
                    logger.debug(f"Mailchimp upsert response for {email}:")
                    logger.debug(json.dumps(response_body, indent=2))
                    
                    # ─── COMPREHENSIVE COMPLIANCE DETECTION ────────────────────────
                    detail_text = str(response_body.get("detail", "")).lower()
                    compliance_indicators = [
                        response_body.get("title") == "Member In Compliance State",
                        "compliance state" in detail_text,
                        "unsubscribe, bounce" in detail_text,
                        response_body.get("status") == "400"
                    ]

                    if any(compliance_indicators):
                        logger.info(
                            f"ℹ️ Respecting Mailchimp compliance state for {email}: "
                            f"{response_body.get('detail', 'Contact cannot be subscribed')}"
                        )
                        return "compliance_state"  # Expected behavior, no warning or Teams alert

                    # ─── NON-COMPLIANCE WARNINGS (only if not compliance) ──────────
                    if "errors" in response_body:
                        logger.warning(f"⚠️ Mailchimp responded with warnings for {email}: {response_body['errors']}")
                    if "detail" in response_body:
                        logger.warning(f"⚠️ Mailchimp returned detail message for {email}: {response_body['detail']}")
                    if "status" in response_body:
                        status = response_body["status"]
                        logger.debug(f"Contact {email} status after upsert: {status}")
                        if status not in ["subscribed", "unsubscribed"]:
                            logger.warning(
                                f"⚠️ Contact {email} has status '{status}' which may not be visible in Mailchimp UI"
                            )
                            notify_warning(
                                "Contact has unexpected status in Mailchimp",
                                {"email": email, "status": status, "expected_statuses": ["subscribed", "unsubscribed"]}
                            )
                except ValueError:
                    logger.debug(f"No JSON response body for {email}")
                
                # Handle successful responses
                if response.status_code in (200, 201):
                    logger.debug(f"Successfully upserted contact: {email} (Status: {response.status_code})")
                    
                    # Apply tag with configurable delay to ensure the member is fully created/updated
                    upsert_delay = perf_config.mailchimp_upsert_delay
                    logger.debug(f"Waiting {upsert_delay}s before applying tag to {email}")
                    if apply_mailchimp_tag(email, was_archived):
                        logger.debug(f"Successfully tagged {email} with '{MAILCHIMP_TAG}'")
                        
                        # Verify contact status after tagging
                        logger.debug(f"Verifying contact status for {email} after tagging")
                        contact_data = get_mailchimp_contact_status(email)
                        if not contact_data:
                            logger.error(f"❌ Failed to verify contact status for {email} after successful upsert and tagging")
                            notify_error("Contact verification failed after upsert",
                                       {"email": email, "step": "post_upsert_verification"})
                    else:
                        logger.warning(f"Failed to tag {email} - the contact was upserted but tagging failed")
                        notify_warning("Contact upserted but tag application failed",
                                     {"email": email, "tag": MAILCHIMP_TAG})
                    
                    return "success"
                elif response.status_code == 400:
                    # Handle 400 errors - check for permanent failures BEFORE raising exceptions
                    try:
                        response_body = response.json()
                        detail_text = str(response_body.get("detail", "")).lower()
                        
                        # Expanded permanent failure indicators to catch all non-critical rejections
                        permanent_failure_indicators = [
                            "cannot be subscribed" in detail_text,
                            ("unsubscribed" in detail_text and "bounced" in detail_text),
                            "under review" in detail_text,
                            "address is bounced" in detail_text,
                            "looks fake or invalid" in detail_text,  # catch invalid email rejects
                            "fake email" in detail_text,
                            "invalid email" in detail_text
                        ]
                        
                        if any(permanent_failure_indicators):
                            logger.warning(f"Non-critical Mailchimp rejection for {email}: {detail_text} - archiving instead of erroring")
                            
                            # Archive the contact instead of treating as error
                            try:
                                archive_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
                                archive_response = requests.delete(archive_url, auth=auth, headers=headers)
                                
                                if archive_response.status_code in [200, 204, 404]:  # 404 means already archived
                                    logger.info(f"✅ Archived rejected contact: {email} (reason: permanent failure)")
                                    
                                    # Only send Teams notification for non-fake email rejections
                                    fake_email_indicators = [
                                        "looks fake or invalid" in detail_text,
                                        "fake email" in detail_text,
                                        "invalid email" in detail_text
                                    ]
                                    
                                    if not any(fake_email_indicators):
                                        notify_warning("Archived contact due to permanent Mailchimp rejection", 
                                                     {"email": email, "reason": detail_text[:100], "status": "archived"})
                                    else:
                                        logger.debug(f"Skipping Teams notification for fake/invalid email rejection: {email}")
                                    
                                    return "unsubscribed"  # Treat as handled successfully
                                else:
                                    logger.warning(f"Failed to archive {email}, archive response status: {archive_response.status_code}")
                                    return "unsubscribed_failed"  # Failed to archive but still non-critical
                            except Exception as archive_error:
                                logger.warning(f"Exception during archival of {email}: {archive_error}")
                                return "unsubscribed_failed"  # Failed to archive but still non-critical
                        else:
                            # Truly unexpected 400 errors should still be treated as errors and trigger retries
                            logger.error(f"Unexpected 400 error for {email}: {detail_text}")
                            response.raise_for_status()
                    except (ValueError, KeyError) as parse_error:
                        # If we can't parse the response JSON, treat as regular 400 error
                        logger.error(f"Could not parse 400 response for {email}: {parse_error}")
                        response.raise_for_status()
                else:
                    response.raise_for_status()
                
                break  # Break out of retry loop on successful completion
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    notify_warning("Mailchimp upsert API retry due to network issue",
                                 {"email": email, "error": str(e), "attempt": attempt + 1, 
                                  "max_retries": MAX_RETRIES, "retry_delay": RETRY_DELAY})
                    time.sleep(RETRY_DELAY)
                else:
                    notify_error("Mailchimp upsert API failed after max retries",
                               {"email": email, "error": str(e), "max_retries": MAX_RETRIES})
                    raise
        
    except Exception as e:
        logger.error(f"Error upserting Mailchimp contact {email}: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
        return "error"
    
    return "error"


def apply_mailchimp_tag(email: str, was_archived: bool = False) -> bool:
    """
    Apply a tag to a Mailchimp contact.
    Returns True if successful, False otherwise.
    
    Args:
        email: Email address of the contact
        was_archived: True if contact was archived before the upsert (triggers tag clearing)
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
        status = member_data.get("status")
        logger.debug(f"Contact {email} status before tagging: '{status}'")

        # ── If they were archived _before_ our upsert, clear every old tag now
        if was_archived:
            clear_mailchimp_tags(email)
            logger.info(f"🧹 (unarchive) cleared all old tags for {email}")
        else:
            # 1) If the member is STILL archived → clear all old tags, then allow new tag
            if status == "archived":
                clear_mailchimp_tags(email)
            else:
                # 2) SINGLE-TAG RULE: skip if any other HubSpot tag is already present
                existing_tags = [t["name"] for t in member_data.get("tags", [])]
                for et in existing_tags:
                    if et in get_hubspot_import_tags() and et != MAILCHIMP_TAG:
                        logger.info(
                          f"🔒 Skipping tag '{MAILCHIMP_TAG}' for {email}: already has '{et}'"
                        )
                        return True
        
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
                    
                    # Verify tag was actually applied with configurable delay
                    tag_delay = perf_config.mailchimp_tag_delay
                    time.sleep(tag_delay)  # Configurable delay to allow tag processing
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
                            notify_warning("Tag verification failed after successful application",
                                         {"email": email, "tag": MAILCHIMP_TAG, "found_tags": tag_names})
                    
                    return True
                
                break
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for tagging {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    notify_warning("Mailchimp untag API retry due to network issue",
                                 {"email": email, "tag": MAILCHIMP_TAG, "error": str(e), 
                                  "attempt": attempt + 1, "max_retries": MAX_RETRIES, "retry_delay": RETRY_DELAY})
                    time.sleep(RETRY_DELAY)
                else:
                    notify_error("Mailchimp tag API failed after max retries",
                               {"email": email, "tag": MAILCHIMP_TAG, "error": str(e), "max_retries": MAX_RETRIES})
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
                    notify_warning("Mailchimp contact removal API retry due to network issue",
                                 {"email": email, "error": str(e), "attempt": attempt + 1, 
                                  "max_retries": MAX_RETRIES, "retry_delay": RETRY_DELAY})
                    time.sleep(RETRY_DELAY)
                else:
                    notify_error("Mailchimp contact removal API failed after max retries",
                               {"email": email, "error": str(e), "max_retries": MAX_RETRIES})
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
            try:
                response = requests.post(url, auth=auth, headers=headers, json=payload)
                if response.status_code in (200, 204):
                    logger.debug(f"✅ Removed tag '{MAILCHIMP_TAG}' from {email}")
                    return True
                else:
                    logger.warning(f"Failed to remove tag '{MAILCHIMP_TAG}' from {email}: {response.text}")
                    if attempt == MAX_RETRIES - 1:
                        notify_error("Failed to remove tag from contact after max retries",
                                   {"email": email, "tag": MAILCHIMP_TAG, "response": response.text, 
                                    "status_code": response.status_code, "max_retries": MAX_RETRIES})
                    response.raise_for_status()
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp untag API request failed for {email}: {e}. Retrying in {RETRY_DELAY} seconds...")
                    notify_warning("Mailchimp untag API retry due to network issue",
                                 {"email": email, "tag": MAILCHIMP_TAG, "error": str(e), 
                                  "attempt": attempt + 1, "max_retries": MAX_RETRIES, "retry_delay": RETRY_DELAY})
                    time.sleep(RETRY_DELAY)
                else:
                    notify_error("Mailchimp untag API failed after max retries",
                               {"email": email, "tag": MAILCHIMP_TAG, "error": str(e), "max_retries": MAX_RETRIES})
                    raise
    except Exception as e:
        logger.error(f"Error untagging contact {email}: {e}")
        notify_error("Unexpected error during contact untagging",
                   {"email": email, "tag": MAILCHIMP_TAG, "error": str(e)})
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
                    notify_warning("Missing merge fields detected - auto-creating", 
                                 {"missing_fields": missing_fields})
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
                        notify_error("Merge field creation failed after retry", 
                                   {"still_missing": missing_after})
                        sys.exit(1)
                else:
                    logger.info("✅ All required merge fields exist in Mailchimp audience")
                
                # Return the full fields data
                return data
                
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Mailchimp API request failed for merge fields: {e}. Retrying in {RETRY_DELAY} seconds...")
                    notify_warning("Mailchimp merge fields API retry due to network issue",
                                 {"error": str(e), "attempt": attempt + 1, 
                                  "max_retries": MAX_RETRIES, "retry_delay": RETRY_DELAY})
                    time.sleep(RETRY_DELAY)
                else:
                    notify_error("Mailchimp merge fields API failed after max retries",
                               {"error": str(e), "max_retries": MAX_RETRIES})
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

    # 2) v3 response has list data nested under "list" key
    if "list" in meta:
        # v3 Lists API format
        list_data = meta.get("list", {})
        name = list_data.get("name")
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
                    notify_warning("Tag search API retry during rename",
                                 {"old_tag": old_name, "new_tag": new_name, "error": str(e), 
                                  "attempt": attempt + 1, "max_retries": MAX_RETRIES})
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Failed to find tag '{old_name}': {e}")
                    notify_error("Tag search failed after max retries during rename",
                               {"old_tag": old_name, "new_tag": new_name, "error": str(e), "max_retries": MAX_RETRIES})
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
                        notify_warning("Tag rename API retry",
                                     {"old_tag": old_name, "new_tag": new_name, "status_code": response.status_code,
                                      "attempt": attempt + 1, "max_retries": MAX_RETRIES})
                        time.sleep(RETRY_DELAY)
                    else:
                        logger.error(f"❌ Failed to rename tag: {response.status_code}")
                        logger.error(f"Response: {response.text}")
                        notify_error("Tag rename failed after max retries",
                                   {"old_tag": old_name, "new_tag": new_name, "status_code": response.status_code,
                                    "response": response.text, "max_retries": MAX_RETRIES})
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

# ── SINGLE-TAG ENFORCEMENT STATE ───────────────────────────────────────────
from typing import Set  # should already be imported; just confirm

# Track which HubSpot-import tags we've applied this run
processed_tags: Set[str] = set()

def get_hubspot_import_tags() -> Set[str]:
    """
    Derive all possible HubSpot-import tag names (Mailchimp segment names)
    for this sync run, based on HUBSPOT_LIST_IDS.
    """
    tags = set()
    for lid in HUBSPOT_LIST_IDS:
        # fetch_hubspot_list_name returns the segment name
        name = fetch_hubspot_list_name(lid)
        tags.add(name)
    return tags

def get_mailchimp_member(email: str) -> Optional[dict]:
    """Fetch a Mailchimp member by email, or None if not found."""
    subscriber_hash = calculate_subscriber_hash(email.lower())
    url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    resp = requests.get(url,
                        auth=("anystring", MAILCHIMP_API_KEY),
                        headers={"Content-Type": "application/json"})
    return resp.json() if resp.status_code == 200 else None

def clear_mailchimp_tags(email: str):
    """
    Remove ALL tags from an archived contact before re-tagging.
    """
    member = get_mailchimp_member(email)
    if not member or not member.get("tags"):
        return
    subscriber_hash = calculate_subscriber_hash(email.lower())
    url = f"{MAILCHIMP_BASE_URL}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}/tags"
    payload = {
        "tags": [{"name": t["name"], "status": "inactive"} for t in member["tags"]]
    }
    requests.post(url,
                  auth=("anystring", MAILCHIMP_API_KEY),
                  headers={"Content-Type": "application/json"},
                  json=payload)
    logger.info(f"🧹 Cleared all tags for archived contact {email}")

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
    
    # Send initial notification about sync start
    try:
        notify_info("HubSpot → Mailchimp sync started",
                   {"lists_to_process": HUBSPOT_LIST_IDS,
                    "total_lists": len(HUBSPOT_LIST_IDS),
                    "configuration": {
                        "page_size": PAGE_SIZE,
                        "test_limit": TEST_CONTACT_LIMIT,
                        "max_retries": MAX_RETRIES,
                        "retry_delay": RETRY_DELAY
                    }})
    except Exception as e:
        logger.warning(f"Failed to send sync start notification: {e}")
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
                    notify_warning("Tag rename failed - implementing fallback strategy",
                                 {"old_tag": old_name, "new_tag": list_name, "fallback": "untag_old_members"})
                    
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
            # Remember this tag so we never apply more than one per contact
            processed_tags.add(MAILCHIMP_TAG)
        except Exception as e:
            logger.exception("Failed to determine list name for %s: %s", list_id, e)
            notify_error("List processing failed - could not determine list name",
                       {"list_id": list_id, "error": str(e), "error_type": type(e).__name__})
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
            notify_warning("No contacts found or fetch failed for HubSpot list",
                         {"list_id": list_id, "list_name": list_name})
            continue

        # Step 2: Create a set of all HubSpot emails for comparison
        hubspot_emails = {contact["email"].lower() for contact in hubspot_contacts}
        logger.info(f"Found {len(hubspot_emails)} unique emails in HubSpot for list {list_id}")
        # Track these emails globally for final archival cleanup
        all_synced_emails.update(hubspot_emails)

        # Step 3: Upsert all HubSpot contacts to Mailchimp with source list tracking
        # Prepare stats buckets for summary
        stats = defaultdict(list)
        total = len(hubspot_contacts)
        
        # Single progress bar for the entire list
        with tqdm(hubspot_contacts,
                  desc=f"Syncing list {list_id}",
                  unit="contact",
                  ncols=80,
                  leave=True,
                  mininterval=2.0,
                  miniters=100,
                  bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]') as bar:
            for contact in bar:
                result = upsert_mailchimp_contact(contact, source_list_id=list_id)
                
                # Bucket results for end-of-run summary
                if result == "success":
                    stats["successful"].append(contact["email"])
                elif result == "unsubscribed":
                    stats["unsubscribed"].append(contact["email"])
                elif result == "unsubscribed_failed":
                    stats["unsubscribed_failed"].append(contact["email"])
                elif result == "compliance_state":
                    stats["compliance_state"].append(contact["email"])
                elif result == "error":
                    stats["errors"].append(contact["email"])
                else:
                    stats["other"].append(contact["email"])
                    
                time.sleep(0.2)

        # Emit concise end-of-run summary
        logger.info(f"Summary for list {list_id}:")
        logger.info(f"  • {len(stats['successful'])} successful upserts")
        if stats['unsubscribed']:
            sample = stats['unsubscribed'][:5]
            suffix = f"{'...' if len(stats['unsubscribed']) > 5 else ''}"
            logger.info(f"  • {len(stats['unsubscribed'])} unsubscribed/bounced (archived): {sample}{suffix}")
        if stats['unsubscribed_failed']:
            logger.warning(f"  • {len(stats['unsubscribed_failed'])} unsubscribed contacts failed to archive")
        if stats['compliance_state']:
            logger.info(f"  • {len(stats['compliance_state'])} contacts in compliance state (skipped)")
        if stats['errors']:
            sample = stats['errors'][:3]
            suffix = f"{'...' if len(stats['errors']) > 3 else ''}"
            logger.warning(f"  • {len(stats['errors'])} errors: {sample}{suffix}")
        if stats['other']:
            logger.warning(f"  • {len(stats['other'])} other outcomes")

        successful_upserts = len(stats['successful'])
        logger.debug(f"All contacts from list {list_id} tagged with source list ID for anti-remarketing")

        # Step 4: Get current Mailchimp members with our tag
        mailchimp_emails_dict = get_current_mailchimp_emails()
        mailchimp_emails = set(mailchimp_emails_dict.keys())

        # Step 5: Find and untag stale contacts
        emails_to_untag = mailchimp_emails - hubspot_emails
        successful_untags = 0
        
        if emails_to_untag:
            logger.info(f"Found {len(emails_to_untag)} contacts to untag from Mailchimp for list {list_id}")
            
            # 🚨 CRITICAL SAFETY CHECK: Skip untagging in test mode to prevent accidental mass untagging
            if TEST_CONTACT_LIMIT > 0:
                logger.warning(f"🧪 TEST MODE: Skipping untagging of {len(emails_to_untag)} contacts to prevent accidental mass changes")
                logger.warning(f"🧪 TEST MODE: In production, {len(emails_to_untag)} contacts would be untagged")
                successful_untags = 0  # Set to 0 for test mode
            else:
                print(f"\n🧹 Cleaning up {len(emails_to_untag)} stale contacts...")
                
                # Untag contacts with clean progress bar
                for email in tqdm(emails_to_untag,
                                   desc=f"Untagging stale contacts for list {list_id}",
                                   unit="contact",
                                   ncols=80,
                                   miniters=100,
                                   bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'):
                    if untag_mailchimp_contact(email):
                        successful_untags += 1
                    time.sleep(0.2)
                
                print(f"   ✅ Successfully untagged: {successful_untags} contacts")
                logger.info(f"Successfully removed tag from {successful_untags} contacts for list {list_id}")
        else:
            logger.info("No contacts to untag from Mailchimp for list %s", list_id)
        
        logger.info("%s Completed sync for list %s %s", "="*10, list_id, "="*10)
        
        # Send list completion notification
        try:
            notify_info("List sync completed successfully",
                       {"list_id": list_id, "list_name": list_name,
                        "contacts_upserted": successful_upserts,
                        "contacts_untagged": successful_untags,
                        "total_emails_in_list": len(hubspot_emails)})
        except Exception as e:
            logger.warning(f"Failed to send list completion notification: {e}")

    # --- Phase 3: Global cleanup (archive any Mailchimp members not in any synced list) ---
    if ENABLE_MAILCHIMP_ARCHIVAL:
        # 🚨 CRITICAL SAFETY CHECK: Skip global archival in test mode
        if TEST_CONTACT_LIMIT > 0:
            logger.warning(f"🧪 TEST MODE: Skipping global archival cleanup to prevent accidental mass deletion")
            logger.info("⏭️ Global archival cleanup SKIPPED in test mode - Existing Mailchimp contacts preserved")
            to_archive_count = 0
        else:
            logger.info("Starting global archival cleanup: members not in any HubSpot list will be archived")
            all_mc_emails = get_all_mailchimp_emails()
            to_archive = all_mc_emails - all_synced_emails
            if to_archive:
                logger.info(f"Found {len(to_archive)} contacts to archive (no longer in any HubSpot list)")
                
                print(f"\n🗂️ Archiving {len(to_archive)} orphaned contacts...")
                
                archived_count = 0
                # Archive contacts with clean progress bar
                for email in tqdm(to_archive,
                                   desc="Archiving global stale contacts",
                                   unit="contact",
                                   ncols=80,
                                   miniters=100,
                                   bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'):
                    if remove_mailchimp_contact_by_email(email):
                        archived_count += 1
                    time.sleep(0.2)
                
                print(f"   ✅ Successfully archived: {archived_count} contacts")
                logger.info(f"Successfully archived {archived_count} contacts from Mailchimp")
            else:
                logger.info("No Mailchimp contacts to archive; all members are in at least one HubSpot list")
            # Define to_archive for summary
            to_archive_count = len(to_archive) if 'to_archive' in locals() else 0
    else:
        logger.info("⏭️ Global archival cleanup DISABLED - Existing Mailchimp contacts preserved")
        to_archive_count = 0
    
    # Final summary
    logger.info("Multi-list sync complete: %d unique contacts synced, %d contacts archived", len(all_synced_emails), to_archive_count)
    logger.info("All configured HubSpot lists have been synced and cleanup is complete.")
    
    # Send final Teams notification with session summary
    try:
        send_final_notification({
            "sync_status": "completed_successfully" if not had_errors else "completed_with_errors",
            "total_contacts_synced": len(all_synced_emails),
            "total_contacts_archived": to_archive_count,
            "lists_processed": len(HUBSPOT_LIST_IDS),
            "list_ids": HUBSPOT_LIST_IDS
        })
    except Exception as e:
        logger.warning(f"Failed to send final Teams notification: {e}")

    # If any list failed, abort with non-zero exit
    if had_errors:
        logger.critical("Sync finished with errors—failing the process.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Unhandled exception—failing CI.")
        try:
            notify_error("Critical sync failure - unhandled exception",
                       {"error": str(e), "error_type": type(e).__name__})
            send_final_notification({
                "sync_status": "critical_failure",
                "error": str(e),
                "error_type": type(e).__name__
            })
        except Exception as notify_error:
            logger.error(f"Failed to send failure notification: {notify_error}")
        sys.exit(1)

