"""Unit tests for Mailchimp client."""

import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch
from corev2.clients.mailchimp_client import MailchimpClient, MailchimpMemberStatus


@pytest.fixture
def mc_client():
    """Create Mailchimp client for testing."""
    return MailchimpClient(
        api_key="test-key",
        server_prefix="us1",
        audience_id="abc123",
        rate_limit=100.0  # High rate for tests
    )


def test_mailchimp_auth_uses_http_basic(mc_client):
    """
    Test that Mailchimp client uses HTTP Basic auth, not Bearer.
    
    Per Mailchimp docs: API keys use Basic auth (username:apikey base64 encoded).
    https://mailchimp.com/developer/marketing/docs/fundamentals/#authentication
    """
    auth_header = mc_client.default_headers["Authorization"]
    
    # Must be Basic, not Bearer
    assert auth_header.startswith("Basic "), f"Expected 'Basic', got: {auth_header}"
    
    # Decode and verify format
    encoded = auth_header.replace("Basic ", "")
    decoded = base64.b64decode(encoded).decode()
    
    # Format should be "anystring:apikey"
    assert ":" in decoded, "Basic auth must contain ':' separator"
    username, password = decoded.split(":", 1)
    assert password == "test-key", "Password should be the API key"


@pytest.mark.asyncio
async def test_get_member_found(mc_client):
    """Test get_member returns member data when found."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "test@example.com",
            "status": "subscribed",
            "merge_fields": {"FNAME": "John", "LNAME": "Doe"},
            "tags": [{"name": "VIP"}, {"name": "Newsletter"}]
        }
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        result = await mc_client.get_member("test@example.com")
        
        assert result["found"] is True
        assert result["status"] == "subscribed"
        assert result["tags"] == ["VIP", "Newsletter"]
        assert result["merge_fields"]["FNAME"] == "John"


@pytest.mark.asyncio
async def test_get_member_not_found(mc_client):
    """Test get_member returns not found when member doesn't exist."""
    mock_response = {
        "status": 404,
        "headers": {},
        "data": {"title": "Resource Not Found"}
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        result = await mc_client.get_member("notfound@example.com")
        
        assert result["found"] is False
        assert result["status"] is None
        assert result["tags"] == []


@pytest.mark.asyncio
async def test_upsert_member_new(mc_client):
    """Test upsert creates new member."""
    # First get_member returns not found
    mock_get_response = {
        "status": 404,
        "headers": {},
        "data": {}
    }
    
    # PUT creates the member
    mock_put_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "new@example.com",
            "status": "subscribed"
        }
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get, \
         patch.object(mc_client, 'put', new_callable=AsyncMock) as mock_put:
        mock_get.return_value = mock_get_response
        mock_put.return_value = mock_put_response
        
        result = await mc_client.upsert_member(
            "new@example.com",
            merge_fields={"FNAME": "Jane"},
            status_if_new="subscribed"
        )
        
        assert result["success"] is True
        assert result["action"] == "created"
        assert result["status"] == "subscribed"
        
        # Verify PUT was called with correct payload
        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[1]["json"]["email_address"] == "new@example.com"
        assert call_args[1]["json"]["merge_fields"]["FNAME"] == "Jane"


@pytest.mark.asyncio
async def test_upsert_member_never_resubscribe_unsubscribed(mc_client):
    """Test upsert NEVER resubscribes unsubscribed members."""
    # Member exists and is unsubscribed
    mock_get_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "unsubscribed@example.com",
            "status": "unsubscribed",
            "tags": [],
            "merge_fields": {}
        }
    }
    
    # PATCH updates merge_fields only
    mock_patch_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "unsubscribed@example.com",
            "status": "unsubscribed"
        }
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get, \
         patch.object(mc_client, 'patch', new_callable=AsyncMock) as mock_patch, \
         patch.object(mc_client, 'put', new_callable=AsyncMock) as mock_put:
        mock_get.return_value = mock_get_response
        mock_patch.return_value = mock_patch_response
        
        result = await mc_client.upsert_member(
            "unsubscribed@example.com",
            merge_fields={"FNAME": "UpdatedName"}
        )
        
        assert result["success"] is True
        assert result["status"] == "unsubscribed"  # Status preserved
        assert result["action"] == "updated"
        
        # PUT should NOT be called (would change status)
        mock_put.assert_not_called()
        
        # PATCH should be called instead
        mock_patch.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_member_skip_cleaned_without_changes(mc_client):
    """Test upsert skips cleaned members when no merge_fields provided."""
    mock_get_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "cleaned@example.com",
            "status": "cleaned",
            "tags": [],
            "merge_fields": {}
        }
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get, \
         patch.object(mc_client, 'patch', new_callable=AsyncMock) as mock_patch:
        mock_get.return_value = mock_get_response
        
        result = await mc_client.upsert_member("cleaned@example.com")
        
        assert result["success"] is True
        assert result["status"] == "cleaned"
        assert result["action"] == "skipped"
        
        # No API calls should be made
        mock_patch.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_member_never_resubscribe_archived(mc_client):
    """Test upsert NEVER resubscribes archived members (INV-005)."""
    # Member exists and is archived
    mock_get_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "archived@example.com",
            "status": "archived",
            "tags": [],
            "merge_fields": {}
        }
    }
    
    # PATCH updates merge_fields only
    mock_patch_response = {
        "status": 200,
        "headers": {},
        "data": {
            "email_address": "archived@example.com",
            "status": "archived"
        }
    }
    
    with patch.object(mc_client, 'get', new_callable=AsyncMock) as mock_get, \
         patch.object(mc_client, 'patch', new_callable=AsyncMock) as mock_patch, \
         patch.object(mc_client, 'put', new_callable=AsyncMock) as mock_put:
        mock_get.return_value = mock_get_response
        mock_patch.return_value = mock_patch_response
        
        result = await mc_client.upsert_member(
            "archived@example.com",
            merge_fields={"FNAME": "UpdatedName"}
        )
        
        assert result["success"] is True
        assert result["status"] == "archived"  # Status preserved
        assert result["action"] == "updated"
        
        # PUT should NOT be called (would change status)
        mock_put.assert_not_called()
        
        # PATCH should be called instead
        mock_patch.assert_called_once()


@pytest.mark.asyncio
async def test_add_tags(mc_client):
    """Test add_tags adds tags to member."""
    mock_response = {
        "status": 204,
        "headers": {},
        "data": ""
    }
    
    with patch.object(mc_client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await mc_client.add_tags("test@example.com", ["VIP", "Newsletter"])
        
        assert result["success"] is True
        assert result["tags_added"] == ["VIP", "Newsletter"]
        
        # Verify POST payload
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert len(payload["tags"]) == 2
        assert payload["tags"][0]["name"] == "VIP"
        assert payload["tags"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_remove_tags(mc_client):
    """Test remove_tags removes tags from member."""
    mock_response = {
        "status": 204,
        "headers": {},
        "data": ""
    }
    
    with patch.object(mc_client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await mc_client.remove_tags("test@example.com", ["OldTag"])
        
        assert result["success"] is True
        assert result["tags_removed"] == ["OldTag"]
        
        # Verify POST payload
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["tags"][0]["status"] == "inactive"


@pytest.mark.asyncio
async def test_add_tags_empty_list(mc_client):
    """Test add_tags with empty list does nothing."""
    result = await mc_client.add_tags("test@example.com", [])
    
    assert result["success"] is True
    assert result["tags_added"] == []


@pytest.mark.asyncio
async def test_archive_member_success(mc_client):
    """Test archive_member deletes member."""
    mock_response = {
        "status": 204,
        "headers": {},
        "data": ""
    }
    
    with patch.object(mc_client, 'delete', new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = mock_response
        
        result = await mc_client.archive_member("test@example.com")
        
        assert result["success"] is True
        assert result["action"] == "archived"


@pytest.mark.asyncio
async def test_archive_member_404_is_success(mc_client):
    """Test archive_member treats 404 as success (already archived)."""
    mock_response = {
        "status": 404,
        "headers": {},
        "data": {"title": "Resource Not Found"}
    }
    
    with patch.object(mc_client, 'delete', new_callable=AsyncMock) as mock_delete:
        mock_delete.return_value = mock_response
        
        result = await mc_client.archive_member("notfound@example.com")
        
        assert result["success"] is True
        assert result["action"] == "already_archived"


@pytest.mark.asyncio
async def test_subscriber_hash_lowercase(mc_client):
    """Test subscriber hash always uses lowercase email."""
    hash1 = mc_client._subscriber_hash("TEST@EXAMPLE.COM")
    hash2 = mc_client._subscriber_hash("test@example.com")
    
    assert hash1 == hash2
    assert hash1 == "55502f40dc8b7c769880b10874abc9d0"  # MD5 of "test@example.com"
