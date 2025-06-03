#!/usr/bin/env python3
"""
main.py (formerly config.py)

Main control center for HubSpot→Mailchimp sync operations.
Modify settings here and run directly for different sync scenarios.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv(override=True)

# =============================================================================
# 🎮 OPERATIONAL CONTROLS - Modify these for different run types
# =============================================================================

# Run Mode Selection
RUN_MODE = "TEST_RUN"  # Options: "FULL_SYNC", "TEST_RUN", "TAG_RENAME_ONLY"

# HubSpot lists to sync (edit here whenever you need to add/remove a list)
HUBSPOT_LIST_IDS = [
    "692",  # Main prospect list
    # Add more lists here as needed
]

# Test/Development Settings
TEST_CONTACT_LIMIT = 5      # For testing: limit contacts processed
ENABLE_DRY_RUN = False      # Set True to simulate without actual changes

# =============================================================================
# 🔐 API CREDENTIALS
# =============================================================================
# =============================================================================
# 🔐 API CREDENTIALS
# =============================================================================

# HubSpot API token (keep secret)
HUBSPOT_PRIVATE_TOKEN = os.getenv("HUBSPOT_PRIVATE_TOKEN", "")

# Mailchimp API credentials
MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID = os.getenv("MAILCHIMP_LIST_ID", "")
MAILCHIMP_DC = os.getenv("MAILCHIMP_DC", "")

# Teams notification webhook URL
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", 
    "https://prod-00.centralindia.logic.azure.com:443/workflows/87399a7a0ef3483a9c8a3b02d2dead4c/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=E8BgYXwIN9nve4k3-OWDRGUipkv_wSLXHZQjBf7NKOQ"
)

# =============================================================================
# ⚙️ SYNC PARAMETERS
# =============================================================================

# Processing settings
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 20))               # records per HubSpot page
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))           # API retry attempts
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 2))           # seconds between retries

# =============================================================================
# 📋 DATA MAPPING
# =============================================================================

# Mailchimp merge fields to maintain
# Mailchimp merge fields to maintain
REQUIRED_TAGS = [
    "FNAME",    # first name
    "LNAME",    # last name
    "COMPANY",  # company name
    "PHONE",    # phone number
    "ADDRESS",  # street address
    "ADDRESS2", # street address 2
    "CITY",     # city
    "STATE",    # state/region
    "POSTCODE", # postal code
    "COUNTRY",  # country/region
    "BRANCHES"  # branch assignment
]

# =============================================================================
# 🗂️ STORAGE SETTINGS
# =============================================================================

# Logging level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Directory for raw data exports
RAW_DATA_DIR = os.getenv("RAW_DATA_DIR", "raw_data")

# =============================================================================
# 🚀 EXECUTION FUNCTIONS
# =============================================================================

def clean_workspace():
    """Clean logs and raw_data directories for fresh start"""
    import shutil
    
    directories = ["logs", "raw_data"]
    for directory in directories:
        if os.path.exists(directory):
            shutil.rmtree(directory)
            print(f"🧹 Cleaned {directory}/")
        os.makedirs(directory, exist_ok=True)
        print(f"📁 Created fresh {directory}/")

def run_sync():
    """Execute the main sync operation"""
    from . import sync
    
    print(f"🎮 Running in {RUN_MODE} mode")
    print(f"📋 Processing {len(HUBSPOT_LIST_IDS)} HubSpot list(s)")
    if RUN_MODE == "TEST_RUN":
        print(f"🧪 Test mode: Limited to {TEST_CONTACT_LIMIT} contacts")
    
    sync.main()

def main():
    """Main execution function"""
    print("="*60)
    print("🎯 HUBSPOT → MAILCHIMP SYNC CONTROL CENTER")
    print("="*60)
    
    # Show current settings
    print(f"Mode: {RUN_MODE}")
    print(f"Lists: {HUBSPOT_LIST_IDS}")
    print(f"Test Limit: {TEST_CONTACT_LIMIT if RUN_MODE == 'TEST_RUN' else 'N/A'}")
    print("-"*60)
    
    # Execute based on mode
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean_workspace()
        print("✅ Workspace cleaned. Run again without --clean to sync.")
    else:
        run_sync()
        print("✅ Sync completed.")

if __name__ == "__main__":
    main()
