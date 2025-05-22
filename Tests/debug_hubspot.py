#!/usr/bin/env python
"""
Debug script to diagnose HubSpot list access issues
"""

import os
import json
import requests
from dotenv import load_dotenv

# Load environment variables
print("Loading .env file...")
load_dotenv(override=True)

# HubSpot configuration
HUBSPOT_PRIVATE_TOKEN = os.getenv("HUBSPOT_PRIVATE_TOKEN")
HUBSPOT_LIST_ID = os.getenv("HUBSPOT_LIST_ID")

print(f"HubSpot Private Token: {'*' * 8 if HUBSPOT_PRIVATE_TOKEN else 'Not set'}")
print(f"HubSpot List ID: {HUBSPOT_LIST_ID}")

# Set up headers for API requests
headers = {
    "Authorization": f"Bearer {HUBSPOT_PRIVATE_TOKEN}",
    "Content-Type": "application/json"
}

# Test 1: Verify authentication by getting account details
print("\n=== TEST 1: Verify Authentication ===")
account_url = "https://api.hubapi.com/integrations/v1/me"
try:
    response = requests.get(account_url, headers=headers)
    print(f"Account API Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Account: {data.get('portalId')} - {data.get('portal_name', 'Unknown')}")
        print("✅ Authentication successful")
    else:
        print(f"❌ Authentication failed: {response.text}")
except Exception as e:
    print(f"❌ Error checking authentication: {e}")

"""
Only CRM v3 List memberships test (legacy V1 checks removed)
"""
# Test 2: CRM v3 Lists API Test - fetch first few memberships
print("\n=== TEST 2: CRM v3 Lists API Test ===")
crm_lists_url = f"https://api.hubapi.com/crm/v3/lists/{HUBSPOT_LIST_ID}/memberships"
params = {"limit": 5}
try:
    response = requests.get(crm_lists_url, headers=headers, params=params)
    print(f"CRM v3 Lists API Status Code: {response.status_code}")
    print(f"Request URL: {response.url}")
    response.raise_for_status()
    data = response.json()
    results = data.get("results", [])
    print(f"Results returned: {len(results)}")
    for i, m in enumerate(results, 1):
        # HubSpot v3 Membership uses 'recordId' for contact VID
        print(f"{i}: VID = {m.get('recordId')}")
    # Save full response for inspection
    with open(f"hubspot_list_{HUBSPOT_LIST_ID}_memberships_debug.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Response saved to hubspot_list_{HUBSPOT_LIST_ID}_memberships_debug.json")
except Exception as e:
    print(f"❌ Error fetching CRM v3 list memberships: {e}")

print("\nDiagnostics complete. Check the output files for more details.")
