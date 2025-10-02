#!/usr/bin/env python3
"""
Test script to verify Mailchimp archival persistence
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import MAILCHIMP_API_KEY, MAILCHIMP_LIST_ID, MAILCHIMP_DC
import requests
import json

def test_mailchimp_contact_status(email: str):
    """Test if a contact is archived in Mailchimp and stays archived"""
    
    # Create Mailchimp API session
    base_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"
    headers = {'Authorization': f'apikey {MAILCHIMP_API_KEY}'}
    
    # Generate subscriber hash (MD5 of lowercase email)
    import hashlib
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    
    # Check current status
    url = f"{base_url}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    
    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            status = data.get('status', 'unknown')
            print(f"✅ Contact {email} found with status: {status}")
            return status
        elif response.status_code == 404:
            print(f"❌ Contact {email} not found in Mailchimp")
            return "not_found"
        else:
            print(f"⚠️ Error checking {email}: {response.status_code} - {response.text}")
            return "error"
            
    except Exception as e:
        print(f"❌ Exception checking {email}: {e}")
        return "error"

def test_archive_contact(email: str):
    """Test archiving a contact"""
    
    base_url = f"https://{MAILCHIMP_DC}.api.mailchimp.com/3.0"
    headers = {'Authorization': f'apikey {MAILCHIMP_API_KEY}'}
    
    import hashlib
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    
    # Archive the contact
    url = f"{base_url}/lists/{MAILCHIMP_LIST_ID}/members/{subscriber_hash}"
    payload = {"status": "archived"}
    
    try:
        response = requests.patch(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            print(f"✅ Successfully archived {email}")
            return True
        else:
            print(f"❌ Failed to archive {email}: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Exception archiving {email}: {e}")
        return False

if __name__ == "__main__":
    # Test with a known excluded email from the logs
    test_email = "homefromhomeedgware@gmail.com"  # This was shown being archived in the logs
    
    print(f"🔍 Testing Mailchimp archival persistence for: {test_email}")
    print("="*60)
    
    # Check current status
    current_status = test_mailchimp_contact_status(test_email)
    
    if current_status == "not_found":
        print("❌ Contact not found - can't test archival persistence")
    elif current_status == "archived":
        print("✅ Contact is currently archived - this is good!")
        print("🤔 But why was it being archived again in the logs?")
    elif current_status in ["subscribed", "unsubscribed", "cleaned", "pending"]:
        print(f"⚠️ Contact status is '{current_status}' - not archived!")
        print("🔧 This explains why it gets archived on every run")
        
        # Try to archive it
        print("\n🔧 Attempting to archive the contact...")
        if test_archive_contact(test_email):
            print("✅ Contact archived successfully")
            
            # Check status again
            print("\n🔍 Checking status after archival...")
            new_status = test_mailchimp_contact_status(test_email)
            
            if new_status == "archived":
                print("✅ Contact confirmed archived - persistence test passed")
            else:
                print("❌ Contact not archived after archival attempt - this is the bug!")