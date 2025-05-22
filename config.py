"""
config.py

Central configuration for HubSpot→Mailchimp sync.
Loadable via import in sync.py.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# HubSpot API
HUBSPOT_PRIVATE_TOKEN = os.getenv("HUBSPOT_PRIVATE_TOKEN", "")
# Comma-separated HubSpot List IDs to sync (e.g. "123,456"); falls back to single HUBSPOT_LIST_ID
HUBSPOT_LIST_IDS = [s.strip() for s in os.getenv("HUBSPOT_LIST_IDS", os.getenv("HUBSPOT_LIST_ID", "")).split(",") if s.strip()]

# Mailchimp API
MAILCHIMP_API_KEY = os.getenv("MAILCHIMP_API_KEY", "")
MAILCHIMP_LIST_ID = os.getenv("MAILCHIMP_LIST_ID", "")
MAILCHIMP_DC = os.getenv("MAILCHIMP_DC", "")

# Sync parameters
PAGE_SIZE = int(os.getenv("PAGE_SIZE", 20))               # records per HubSpot page
TEST_CONTACT_LIMIT = int(os.getenv("TEST_CONTACT_LIMIT", 30))  # 0 = no limit
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", 2))           # seconds between retries

# Mailchimp audience merge‐fields to enforce
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

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
