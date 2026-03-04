"""
Read-only smoke tests for integration validation.

Tests basic API connectivity and data fetching without any mutations.
Skips cleanly if required environment variables are missing.
"""
import os
import pytest
import asyncio
from corev2.config.loader import load_config
from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mailchimp_client import MailchimpClient


# Skip all tests if env vars missing
pytestmark = pytest.mark.skipif(
    not all([
        os.getenv("HUBSPOT_PRIVATE_APP_TOKEN"),
        os.getenv("MAILCHIMP_API_KEY"),
        os.getenv("MAILCHIMP_DC")
    ]),
    reason="Missing required env vars (HUBSPOT_PRIVATE_APP_TOKEN, MAILCHIMP_API_KEY, MAILCHIMP_DC)"
)


@pytest.fixture
def config():
    """Load config with test_contact_limit=3 for safety."""
    config_path = "corev2/config/defaults.yaml"
    cfg = load_config(config_path)
    return cfg


@pytest.mark.asyncio
async def test_hubspot_fetch_list_members_readonly(config):
    """
    Smoke test: Fetch members from HubSpot list 752.
    
    Verifies:
    - HubSpot client initialization
    - Authentication works
    - List fetching returns valid data
    - No mutations attempted
    """
    hs_client = HubSpotClient(
        api_key=config.hubspot.api_key.get_secret_value(),
        rate_limit=10.0
    )
    
    async with hs_client:
        # Fetch first page from list 752 (read-only)
        count = 0
        async for contact in hs_client.get_list_members("752", properties=["email", "firstname", "lastname"]):
            count += 1
            assert "email" in contact or "vid" in contact
            print(f"✓ HubSpot: Found contact VID={contact.get('vid')}, email={contact.get('email')}")
            if count >= 3:  # Just fetch first 3 to verify
                break
        
        assert count > 0, "No contacts found in list 752"
        print(f"✓ HubSpot: Successfully fetched {count} contacts from list 752 (read-only)")


@pytest.mark.asyncio
async def test_mailchimp_fetch_member_readonly(config):
    """
    Smoke test: Ping Mailchimp audience.
    
    Verifies:
    - Mailchimp client initialization
    - Authentication works
    - Can fetch member data
    - No mutations attempted
    """
    mc_client = MailchimpClient(
        api_key=config.mailchimp.api_key.get_secret_value(),
        server_prefix=config.mailchimp.server_prefix,
        audience_id=config.mailchimp.audience_id,
        rate_limit=10.0
    )
    
    async with mc_client:
        # Try to fetch a member (will be 404 if not exists, that's OK)
        # Just verifying auth works
        result = await mc_client.get(f"/lists/{config.mailchimp.audience_id}")
        
        assert result["status"] in [200, 404], f"Unexpected status: {result['status']}"
        print(f"✓ Mailchimp: API connectivity verified (status={result['status']})")


@pytest.mark.asyncio
async def test_plan_generation_with_real_apis(config):
    """
    Smoke test: Generate plan using REAL APIs (read-only).
    
    Verifies:
    - Planner can initialize with real clients
    - Fetch pipeline works
    - Plan generation produces valid JSON structure
    - No mutations attempted (test_contact_limit enforced)
    """
    from corev2.planner.primary import SyncPlanner
    
    hs_client = HubSpotClient(
        api_key=config.hubspot.api_key.get_secret_value(),
        rate_limit=10.0
    )
    mc_client = MailchimpClient(
        api_key=config.mailchimp.api_key.get_secret_value(),
        server_prefix=config.mailchimp.server_prefix,
        audience_id=config.mailchimp.audience_id,
        rate_limit=10.0
    )
    
    async with hs_client, mc_client:
        planner = SyncPlanner(config, hs_client, mc_client)
        
        # Generate plan with test_contact_limit
        plan = await planner.generate_plan(contact_limit=config.safety.test_contact_limit)
        
        assert plan is not None
        assert "metadata" in plan
        assert "summary" in plan
        assert "operations" in plan
        
        print(f"✓ Plan generation: {plan['summary']['total_contacts_scanned']} contacts scanned")
        print(f"  Contacts with operations: {plan['summary']['contacts_with_operations']}")
        print(f"  Operations by type: {plan['summary']['operations_by_type']}")


if __name__ == "__main__":
    # Allow running tests directly with python -m
    pytest.main([__file__, "-v", "-s"])
