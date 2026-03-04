"""Unit tests for HubSpot client."""

import pytest
from unittest.mock import AsyncMock, patch
from corev2.clients.hubspot_client import HubSpotClient


@pytest.fixture
def hs_client():
    """Create HubSpot client for testing."""
    return HubSpotClient(
        api_key="test-key",
        rate_limit=100.0  # High rate for tests
    )


@pytest.mark.asyncio
async def test_get_list_members_single_page(hs_client):
    """Test get_list_members with single page of results."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {
            "contacts": [
                {
                    "vid": 12345,
                    "identity-profiles": [{
                        "identities": [{
                            "type": "EMAIL",
                            "value": "test1@example.com"
                        }]
                    }],
                    "properties": {
                        "firstname": {"value": "John"},
                        "lastname": {"value": "Doe"}
                    },
                    "list-memberships": {"718": True}
                },
                {
                    "vid": 12346,
                    "identity-profiles": [{
                        "identities": [{
                            "type": "EMAIL",
                            "value": "test2@example.com"
                        }]
                    }],
                    "properties": {
                        "firstname": {"value": "Jane"},
                        "lastname": {"value": "Smith"}
                    },
                    "list-memberships": {"718": True}
                }
            ],
            "has-more": False
        }
    }
    
    with patch.object(hs_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        contacts = []
        async for contact in hs_client.get_list_members("718"):
            contacts.append(contact)
        
        assert len(contacts) == 2
        assert contacts[0]["vid"] == 12345
        assert contacts[0]["email"] == "test1@example.com"
        assert contacts[1]["vid"] == 12346


@pytest.mark.asyncio
async def test_get_list_members_pagination(hs_client):
    """Test get_list_members with cursor pagination."""
    # First page
    mock_response_page1 = {
        "status": 200,
        "headers": {},
        "data": {
            "contacts": [
                {
                    "vid": 1,
                    "identity-profiles": [{"identities": [{"value": "page1@example.com"}]}],
                    "properties": {},
                    "list-memberships": {}
                }
            ],
            "has-more": True,
            "vid-offset": 1001
        }
    }
    
    # Second page
    mock_response_page2 = {
        "status": 200,
        "headers": {},
        "data": {
            "contacts": [
                {
                    "vid": 2,
                    "identity-profiles": [{"identities": [{"value": "page2@example.com"}]}],
                    "properties": {},
                    "list-memberships": {}
                }
            ],
            "has-more": False
        }
    }
    
    with patch.object(hs_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_response_page1, mock_response_page2]
        
        contacts = []
        async for contact in hs_client.get_list_members("718"):
            contacts.append(contact)
        
        assert len(contacts) == 2
        assert contacts[0]["email"] == "page1@example.com"
        assert contacts[1]["email"] == "page2@example.com"
        
        # Verify pagination parameters
        assert mock_get.call_count == 2
        second_call_params = mock_get.call_args_list[1][1]["params"]
        assert second_call_params["vidOffset"] == 1001


@pytest.mark.asyncio
async def test_get_contact_by_email_found(hs_client):
    """Test get_contact_by_email when contact exists."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {
            "vid": 12345,
            "properties": {
                "email": {"value": "test@example.com"},
                "firstname": {"value": "John"},
                "lastname": {"value": "Doe"}
            }
        }
    }
    
    with patch.object(hs_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        result = await hs_client.get_contact_by_email("test@example.com")
        
        assert result["found"] is True
        assert result["vid"] == 12345
        assert result["email"] == "test@example.com"
        assert "firstname" in result["properties"]


@pytest.mark.asyncio
async def test_get_contact_by_email_not_found(hs_client):
    """Test get_contact_by_email when contact doesn't exist."""
    mock_response = {
        "status": 404,
        "headers": {},
        "data": {"message": "contact does not exist"}
    }
    
    with patch.object(hs_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        result = await hs_client.get_contact_by_email("notfound@example.com")
        
        assert result["found"] is False
        assert result["vid"] is None
        assert result["email"] == "notfound@example.com"


@pytest.mark.asyncio
async def test_add_contact_to_list(hs_client):
    """Test add_contact_to_list adds contact to list."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {"updated": [12345]}
    }
    
    with patch.object(hs_client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await hs_client.add_contact_to_list("718", 12345)
        
        assert result["success"] is True
        assert result["list_id"] == "718"
        assert result["contact_vid"] == 12345
        
        # Verify payload
        call_args = mock_post.call_args
        assert call_args[1]["json"]["vids"] == [12345]


@pytest.mark.asyncio
async def test_remove_contact_from_list(hs_client):
    """Test remove_contact_from_list removes contact from list."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {"updated": [12345]}
    }
    
    with patch.object(hs_client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await hs_client.remove_contact_from_list("718", 12345)
        
        assert result["success"] is True
        assert result["list_id"] == "718"
        assert result["contact_vid"] == 12345


@pytest.mark.asyncio
async def test_update_contact_property(hs_client):
    """Test update_contact_property updates a contact property."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {"vid": 12345}
    }
    
    with patch.object(hs_client, 'post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        result = await hs_client.update_contact_property(
            12345,
            "ORI_LISTS",
            "718,784"
        )
        
        assert result["success"] is True
        assert result["contact_vid"] == 12345
        assert result["property"] == "ORI_LISTS"
        
        # Verify payload
        call_args = mock_post.call_args
        props = call_args[1]["json"]["properties"]
        assert props[0]["property"] == "ORI_LISTS"
        assert props[0]["value"] == "718,784"


@pytest.mark.asyncio
async def test_get_list_members_with_custom_properties(hs_client):
    """Test get_list_members with custom properties."""
    mock_response = {
        "status": 200,
        "headers": {},
        "data": {
            "contacts": [{
                "vid": 1,
                "identity-profiles": [{"identities": [{"value": "test@example.com"}]}],
                "properties": {
                    "email": {"value": "test@example.com"},
                    "custom_field": {"value": "custom_value"}
                },
                "list-memberships": {}
            }],
            "has-more": False
        }
    }
    
    with patch.object(hs_client, 'get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        contacts = []
        async for contact in hs_client.get_list_members(
            "718",
            properties=["email", "custom_field"]
        ):
            contacts.append(contact)
        
        assert len(contacts) == 1
        
        # Verify custom properties were requested
        call_params = mock_get.call_args[1]["params"]
        assert "custom_field" in call_params["property"]
