"""
HubSpot API Client.

Fully migrated to V3 APIs — zero V1 dependencies.

Migration completed April 2026:
- get_contact_by_email: migrated from GET /contacts/v1/contact/email/{email}/profile
  to POST /crm/v3/objects/contacts/search (V1 sunset: April 30, 2026)
- update_contact_property: migrated from POST /contacts/v1/contact/vid/{vid}/profile
  to PATCH /crm/v3/objects/contacts/{id} (future-proofing)

API versions used:
- Lists:    /crm/v3/lists/{id}/memberships
- Contacts: /crm/v3/objects/contacts/batch/read
- Search:   /crm/v3/objects/contacts/search
- Add/Remove list: /crm/v3/lists/{id}/memberships/add|remove  (PUT)
- Update contact:  /crm/v3/objects/contacts/{id}  (PATCH)
"""

import logging
from typing import Dict, List, Optional, Any, AsyncIterator

from .http_base import HTTPBaseClient

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100       # HubSpot batch read max
_PAGE_SIZE = 100        # List memberships per page max


class HubSpotClient(HTTPBaseClient):
    """HubSpot API client — fully V3, batch-optimised list member fetching."""

    def __init__(
        self,
        api_key: str,
        rate_limit: float = 10.0,
        max_retries: int = 5,
    ):
        super().__init__(
            service_name="HubSpot",
            base_url="https://api.hubapi.com",
            rate_limit=rate_limit,
            max_retries=max_retries,
        )
        self.api_key = api_key
        self.default_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def get_list_members(
        self,
        list_id: str,
        properties: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Yield all members of a HubSpot list.

        OPTIMISED: Collects all record IDs via pagination then batch-reads
        contact details 100 at a time. ~100x faster than one-by-one fetching.

        Args:
            list_id:    HubSpot list ID (string)
            properties: Contact properties to include

        Yields:
            {"vid", "email", "properties", "list_memberships"}
        """
        if properties is None:
            properties = ["email", "firstname", "lastname", "branches"]

        # Step 1: Collect all record IDs via pagination
        record_ids: List[str] = []
        params: Dict[str, Any] = {"limit": _PAGE_SIZE}
        after = None

        while True:
            if after:
                params["after"] = after
            result = await self.get(f"/crm/v3/lists/{list_id}/memberships", params=params)
            if result["status"] == 404:
                logger.warning(f"HubSpot list {list_id} not found — skipping")
                return
            if result["status"] != 200:
                raise Exception(f"HubSpot list {list_id}: {result['status']} - {result['data']}")
            data = result["data"]
            for member in data.get("results", []):
                rid = member.get("recordId")
                if rid:
                    record_ids.append(str(rid))
            paging = data.get("paging", {})
            after = paging.get("next", {}).get("after")
            if not after:
                break

        if not record_ids:
            return

        # Step 2: Batch read contact details
        for i in range(0, len(record_ids), _BATCH_SIZE):
            batch = record_ids[i:i + _BATCH_SIZE]
            result = await self.post(
                "/crm/v3/objects/contacts/batch/read",
                json={
                    "properties": properties,
                    "inputs": [{"id": rid} for rid in batch],
                },
            )
            if result["status"] not in (200, 201, 207):
                raise Exception(f"HubSpot batch read: {result['status']} - {result['data']}")

            for contact in result["data"].get("results", []):
                props = contact.get("properties", {})
                email = props.get("email")
                if not email:
                    continue
                yield {
                    "vid": contact.get("id"),
                    "email": email,
                    "properties": props,
                    "list_memberships": {list_id: True},
                }

    async def get_contact_by_email(
        self,
        email: str,
        properties: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get a contact by email address using V3 Search API.

        Migrated from V1 GET /contacts/v1/contact/email/{email}/profile
        to V3 POST /crm/v3/objects/contacts/search.
        V1 endpoint sunset: April 30, 2026.

        Args:
            email:      Contact email address
            properties: Properties to fetch

        Returns:
            {"found": bool, "vid": str|None, "email": str, "properties": dict}
        """
        if properties is None:
            properties = ["email", "firstname", "lastname"]

        result = await self.post(
            "/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "email",
                        "operator": "EQ",
                        "value": email.lower()
                    }]
                }],
                "properties": properties,
                "limit": 1
            }
        )

        if result["status"] == 404:
            return {"found": False, "vid": None, "email": email, "properties": {}}

        if result["status"] != 200:
            raise Exception(
                f"HubSpot get_contact_by_email({email}): {result['status']} - {result['data']}"
            )

        results = result["data"].get("results", [])
        if not results:
            return {"found": False, "vid": None, "email": email, "properties": {}}

        contact = results[0]
        return {
            "found": True,
            "vid": contact.get("id"),
            "email": email,
            "properties": contact.get("properties", {}),
        }

    async def add_contact_to_list(
        self,
        list_id: str,
        contact_vid: Any
    ) -> Dict[str, Any]:
        """
        Add a contact to a static HubSpot list (V3).

        Args:
            list_id:     HubSpot list ID
            contact_vid: Contact record ID

        Returns:
            {"success": bool, "list_id": str, "contact_vid": any}
        """
        result = await self.put(
            f"/crm/v3/lists/{list_id}/memberships/add",
            json=[str(contact_vid)],
        )
        if result["status"] in (200, 201, 204):
            return {"success": True, "list_id": list_id, "contact_vid": contact_vid}
        raise Exception(
            f"HubSpot add_to_list({list_id}, {contact_vid}): {result['status']} - {result['data']}"
        )

    async def remove_contact_from_list(
        self,
        list_id: str,
        contact_vid: Any
    ) -> Dict[str, Any]:
        """
        Remove a contact from a static HubSpot list (V3).

        Args:
            list_id:     HubSpot list ID
            contact_vid: Contact record ID

        Returns:
            {"success": bool, "list_id": str, "contact_vid": any}
        """
        result = await self.put(
            f"/crm/v3/lists/{list_id}/memberships/remove",
            json=[str(contact_vid)],
        )
        if result["status"] in (200, 201, 204):
            return {"success": True, "list_id": list_id, "contact_vid": contact_vid}
        raise Exception(
            f"HubSpot remove_from_list({list_id}, {contact_vid}): {result['status']} - {result['data']}"
        )

    async def update_contact_property(
        self,
        contact_vid: Any,
        property_name: str,
        value: str
    ) -> Dict[str, Any]:
        """
        Update a single contact property using V3 API.

        Migrated from V1 POST /contacts/v1/contact/vid/{vid}/profile
        to V3 PATCH /crm/v3/objects/contacts/{id}.

        Args:
            contact_vid:   Contact record ID
            property_name: HubSpot property name (e.g. "ORI_LISTS")
            value:         New value

        Returns:
            {"success": bool, "contact_vid": any, "property": str}
        """
        result = await self.patch(
            f"/crm/v3/objects/contacts/{contact_vid}",
            json={
                "properties": {
                    property_name: value
                }
            }
        )
        if result["status"] in (200, 201, 204):
            return {"success": True, "contact_vid": contact_vid, "property": property_name}
        raise Exception(
            f"HubSpot update_property(VID={contact_vid}, {property_name}): "
            f"{result['status']} - {result['data']}"
        )