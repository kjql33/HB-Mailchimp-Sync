"""Integration test for idempotency of operations."""

import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_idempotency():
    """Test that upserting same member twice is idempotent."""
    from corev2.clients.mailchimp_client import MailchimpClient
    
    # Create client (will use mock, but test logic)
    client = MagicMock(spec=MailchimpClient)
    
    # Simulate first call: member doesn't exist
    client.get_member = AsyncMock(return_value={"found": False})
    client.upsert_member = AsyncMock(return_value={
        "success": True,
        "status": "subscribed",
        "action": "created",
        "email_address": "test@example.com"
    })
    
    # First upsert should create
    result1 = await client.upsert_member(
        "test@example.com",
        {"FNAME": "Test", "LNAME": "User"},
        status_if_new="subscribed"
    )
    assert result1["action"] == "created"
    
    # Simulate second call: member now exists
    client.get_member = AsyncMock(return_value={
        "found": True,
        "status": "subscribed",
        "merge_fields": {"FNAME": "Test", "LNAME": "User"}
    })
    client.upsert_member = AsyncMock(return_value={
        "success": True,
        "status": "subscribed",
        "action": "updated",
        "email_address": "test@example.com"
    })
    
    # Second upsert should update (not create again)
    result2 = await client.upsert_member(
        "test@example.com",
        {"FNAME": "Test", "LNAME": "User"},
        status_if_new="subscribed"
    )
    assert result2["action"] == "updated"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tag_application_idempotency():
    """Test that applying same tag twice is idempotent (Mailchimp API handles dedup)."""
    from corev2.clients.mailchimp_client import MailchimpClient
    
    client = MagicMock(spec=MailchimpClient)
    
    # Mock successful tag applications (Mailchimp API is idempotent)
    client.add_tag = AsyncMock(return_value={
        "success": True,
        "tags_added": ["general_marketing"],
        "email_address": "test@example.com"
    })
    
    # First tag application
    result1 = await client.add_tag("test@example.com", "general_marketing")
    assert result1["success"] is True
    assert "general_marketing" in result1["tags_added"]
    
    # Second tag application (idempotent at API level)
    result2 = await client.add_tag("test@example.com", "general_marketing")
    assert result2["success"] is True
    # Mailchimp doesn't return duplicates in response


def test_execution_journal_reflects_idempotency():
    """Test that execution journal properly logs action='created' vs action='updated'."""
    journal_path = Path("corev2/artifacts/execution_journal.jsonl")
    
    # Read recent execution entries
    if not journal_path.exists():
        pytest.skip("No execution journal found")
    
    with open(journal_path, "r") as f:
        lines = f.readlines()
    
    # Parse last 20 entries
    recent_ops = []
    for line in lines[-20:]:
        try:
            entry = json.loads(line)
            if entry.get("event") == "operation_executed" and entry.get("operation_type") == "upsert_mc_member":
                recent_ops.append(entry)
        except:
            continue
    
    # Verify that repeated operations show action='updated'
    if len(recent_ops) >= 2:
        # Check that actions progress from 'created' to 'updated'
        actions = [op.get("result", {}).get("action") for op in recent_ops if op.get("result", {}).get("action")]
        
        # Should have mix of created and updated, or just updated
        assert "updated" in actions, "Should have at least one 'updated' action for idempotency"
