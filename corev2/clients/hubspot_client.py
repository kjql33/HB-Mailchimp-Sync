"""
HubSpot API Client for V2 Sync System.

Handles:
- List membership fetching (with cursor pagination)
- Contact retrieval by email
- List membership management (add/remove)
- Structured responses (no raw HTTP leakage)
"""

from typing import Dict, List, Optional, Any, AsyncIterator
from .http_base import HTTPBaseClient


class HubSpotClient(HTTPBaseClient):
    """HubSpot API client with retry/rate-limit/circuit-breaker."""
    
    def __init__(
        self,
        api_key: str,
        rate_limit: float = 100.0,  # HubSpot allows 100 req/10sec = 10/sec
        max_retries: int = 5
    ):
        """
        Initialize HubSpot client.
        
        Args:
            api_key: HubSpot API key (private app or legacy)
            rate_limit: Max requests per second (default 10)
            max_retries: Max retry attempts (default 5)
        """
        base_url = "https://api.hubapi.com"
        super().__init__(
            service_name="HubSpot",
            base_url=base_url,
            rate_limit=rate_limit,
            max_retries=max_retries
        )
        self.api_key = api_key
        self.default_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    async def get_list_members(
        self,
        list_id: str,
        properties: Optional[List[str]] = None,
        limit: int = 100
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Get all members of a HubSpot list (with cursor pagination).
        
        Uses v3 lists API for list memberships, then fetches contact details via v3 contacts API.
        
        Args:
            list_id: HubSpot list ID
            properties: Contact properties to fetch (default: email, firstname, lastname)
            limit: Results per page for list memberships (default 100, max 100)
        
        Yields:
            Contact dict with properties and list membership info
        """
        if properties is None:
            properties = ["email", "firstname", "lastname"]
        
        # Use v3 list memberships API
        endpoint = f"/crm/v3/lists/{list_id}/memberships"
        
        params = {
            "limit": min(limit, 100)  # HubSpot max is 100
        }
        
        after = None
        
        while True:
            if after:
                params["after"] = after
            
            result = await self.get(endpoint, params=params)
            
            if result["status"] != 200:
                raise Exception(f"HubSpot API error: {result['status']} - {result['data']}")
            
            data = result["data"]
            results = data.get("results", [])
            
            # Fetch contact details for each record ID
            for member in results:
                record_id = member.get("recordId")
                if not record_id:
                    continue
                
                # Fetch contact details via v3 contacts API
                contact_result = await self.get(
                    f"/crm/v3/objects/contacts/{record_id}",
                    params={"properties": ",".join(properties)}
                )
                
                if contact_result["status"] == 200:
                    contact_data = contact_result["data"]
                    props = contact_data.get("properties", {})
                    yield {
                        "vid": record_id,  # v3 uses recordId instead of vid
                        "email": props.get("email"),
                        "properties": props,
                        "list_memberships": {list_id: True},
                    }
            
            # Check for more pages
            paging = data.get("paging", {})
            after = paging.get("next", {}).get("after")
            if not after:
                break
    
    async def get_contact_by_email(
        self,
        email: str,
        properties: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get contact by email address.
        
        Args:
            email: Contact email address
            properties: Properties to fetch (default: email, firstname, lastname)
        
        Returns:
            {
                "found": bool,
                "vid": int | None,
                "email": str,
                "properties": Dict[str, Any]
            }
        """
        if properties is None:
            properties = ["email", "firstname", "lastname"]
        
        endpoint = f"/contacts/v1/contact/email/{email}/profile"
        
        params = {"property": properties}
        
        result = await self.get(endpoint, params=params)
        
        if result["status"] == 404:
            return {
                "found": False,
                "vid": None,
                "email": email,
                "properties": {}
            }
        
        if result["status"] != 200:
            raise Exception(f"HubSpot API error: {result['status']} - {result['data']}")
        
        data = result["data"]
        return {
            "found": True,
            "vid": data.get("vid"),
            "email": email,
            "properties": data.get("properties", {})
        }
    
    async def add_contact_to_list(
        self,
        list_id: str,
        contact_vid: int
    ) -> Dict[str, Any]:
        """
        Add contact to a list.
        
        Args:
            list_id: HubSpot list ID
            contact_vid: Contact VID
        
        Returns:
            {
                "success": bool,
                "list_id": str,
                "contact_vid": int
            }
        """
        endpoint = f"/contacts/v1/lists/{list_id}/add"
        
        payload = {"vids": [contact_vid]}
        
        result = await self.post(endpoint, json=payload)
        
        if result["status"] in [200, 204]:
            return {
                "success": True,
                "list_id": list_id,
                "contact_vid": contact_vid
            }
        else:
            raise Exception(f"HubSpot add to list failed: {result['status']} - {result['data']}")
    
    async def remove_contact_from_list(
        self,
        list_id: str,
        contact_vid: int
    ) -> Dict[str, Any]:
        """
        Remove contact from a list.
        
        Args:
            list_id: HubSpot list ID
            contact_vid: Contact VID
        
        Returns:
            {
                "success": bool,
                "list_id": str,
                "contact_vid": int
            }
        """
        endpoint = f"/contacts/v1/lists/{list_id}/remove"
        
        payload = {"vids": [contact_vid]}
        
        result = await self.post(endpoint, json=payload)
        
        if result["status"] in [200, 204]:
            return {
                "success": True,
                "list_id": list_id,
                "contact_vid": contact_vid
            }
        else:
            raise Exception(f"HubSpot remove from list failed: {result['status']} - {result['data']}")
    
    async def update_contact_property(
        self,
        contact_vid: int,
        property_name: str,
        value: str
    ) -> Dict[str, Any]:
        """
        Update a single contact property.
        
        Args:
            contact_vid: Contact VID
            property_name: Property name (e.g., "ORI_LISTS")
            value: New value
        
        Returns:
            {
                "success": bool,
                "contact_vid": int,
                "property": str
            }
        """
        endpoint = f"/contacts/v1/contact/vid/{contact_vid}/profile"
        
        payload = {
            "properties": [
                {
                    "property": property_name,
                    "value": value
                }
            ]
        }
        
        result = await self.post(endpoint, json=payload)
        
        if result["status"] in [200, 204]:
            return {
                "success": True,
                "contact_vid": contact_vid,
                "property": property_name
            }
        else:
            raise Exception(f"HubSpot property update failed: {result['status']} - {result['data']}")
