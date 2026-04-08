"""
Mautic REST API Client.

All endpoints verified against live Mautic 7 instance during testing.

Key findings from live testing:
- Tags: PATCH /api/contacts/{id}/edit with {"tags": ["tag1", "-removetag"]}
  Prefix with "-" to remove. Do NOT use /tags/edit (404 in Mautic 7).
- Create: POST /api/contacts/new
- Read:   GET  /api/contacts/{id}  or  GET /api/contacts?search=email:x
- Edit:   PATCH /api/contacts/{id}/edit  (NEVER use PUT - wipes all fields)
- Delete: DELETE /api/contacts/{id}/delete
- DNC:    POST /api/contacts/{id}/dnc/email/add

Auth: HTTP Basic with admin credentials.
"""

import base64
import logging
from typing import AsyncIterator, Dict, List, Optional, Any

from .http_base import HTTPBaseClient

logger = logging.getLogger(__name__)

_DNC_UNSUBSCRIBED = 1
_DNC_BOUNCED = 2
_DNC_CHANNEL = "email"

# Mautic limits
_MAX_FIELD_LENGTH = 64   # firstname/lastname max chars
_PAGE_SIZE = 200         # contacts per page (Mautic max)


class MauticClient(HTTPBaseClient):
    """
    Mautic REST API client.

    Usage:
        async with MauticClient(base_url, username, password) as client:
            member = await client.get_member("user@example.com")
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        rate_limit: float = 10.0,
        max_retries: int = 5,
    ):
        api_base = base_url.rstrip("/") + "/api"
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        super().__init__(
            service_name="Mautic",
            base_url=api_base,
            rate_limit=rate_limit,
            max_retries=max_retries,
        )
        self.default_headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        }
        # email.lower() → contact_id cache (session-scoped)
        self._id_cache: Dict[str, Optional[int]] = {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_contact_id(self, email: str) -> Optional[int]:
        """Look up Mautic contact ID by email. Results are cached."""
        key = email.lower()
        if key in self._id_cache:
            return self._id_cache[key]

        result = await self.get("/contacts", params={"search": f"email:{key}", "limit": 1})
        if result["status"] != 200:
            self._id_cache[key] = None
            return None

        contacts = result["data"].get("contacts", {})
        if not contacts:
            self._id_cache[key] = None
            return None

        contact_id = int(next(iter(contacts)))
        self._id_cache[key] = contact_id
        return contact_id

    def _invalidate_cache(self, email: str) -> None:
        self._id_cache.pop(email.lower(), None)

    def _derive_status(self, contact: Dict[str, Any]) -> str:
        """Map Mautic contact state to Mailchimp-compatible status string."""
        if not contact.get("isPublished", True):
            return "archived"
        for dnc in contact.get("doNotContact", []):
            if dnc.get("channel") != _DNC_CHANNEL:
                continue
            if dnc.get("reason") == _DNC_BOUNCED:
                return "cleaned"
            if dnc.get("reason") == _DNC_UNSUBSCRIBED:
                return "unsubscribed"
        return "subscribed"

    def _extract_tags(self, contact: Dict[str, Any]) -> List[str]:
        return [t["tag"] for t in contact.get("tags", []) if isinstance(t, dict) and "tag" in t]

    def _get_core_field(self, fields_core: Dict, name: str) -> str:
        v = fields_core.get(name, {})
        if isinstance(v, dict):
            return str(v.get("value", "") or "")
        return str(v or "")

    def _safe_field(self, value: str, max_len: int = _MAX_FIELD_LENGTH) -> str:
        """Truncate field to Mautic's max length."""
        return str(value or "")[:max_len]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_member(self, email: str) -> Dict[str, Any]:
        """
        Get a contact by email address.

        Returns:
            {found, status, tags, merge_fields, email_address, mautic_id}
        """
        contact_id = await self._get_contact_id(email)
        if contact_id is None:
            return {
                "found": False, "status": None, "tags": [],
                "merge_fields": {}, "email_address": email, "mautic_id": None,
            }

        result = await self.get(f"/contacts/{contact_id}")
        if result["status"] == 404:
            self._invalidate_cache(email)
            return {
                "found": False, "status": None, "tags": [],
                "merge_fields": {}, "email_address": email, "mautic_id": None,
            }
        if result["status"] != 200:
            raise Exception(f"Mautic get_member({email}): {result['status']} - {result['data']}")

        contact = result["data"].get("contact", {})
        fields = contact.get("fields", {}).get("core", {})
        return {
            "found": True,
            "status": self._derive_status(contact),
            "tags": self._extract_tags(contact),
            "merge_fields": {
                "FNAME": self._get_core_field(fields, "firstname"),
                "LNAME": self._get_core_field(fields, "lastname"),
            },
            "email_address": email,
            "mautic_id": contact_id,
        }

    async def upsert_member(
        self,
        email: str,
        merge_fields: Optional[Dict[str, Any]] = None,
        status_if_new: str = "subscribed",
    ) -> Dict[str, Any]:
        """
        Create or update a contact.

        Rules:
        - Never resubscribes opted-out or bounced contacts.
        - Restores archived contacts (re-publishes them).
        - Truncates firstname/lastname to 64 chars (Mautic limit).
        - Uses PATCH (never PUT) to avoid wiping existing fields.
        """
        merge_fields = merge_fields or {}
        existing = await self.get_member(email)

        payload: Dict[str, Any] = {"email": email}
        if merge_fields.get("FNAME"):
            payload["firstname"] = self._safe_field(merge_fields["FNAME"])
        if merge_fields.get("LNAME"):
            payload["lastname"] = self._safe_field(merge_fields["LNAME"])

        if existing["found"]:
            status = existing["status"]
            cid = existing["mautic_id"]

            # Never resubscribe opted-out / cleaned contacts
            if status in ("unsubscribed", "cleaned"):
                if len(payload) > 1:  # has fields beyond email
                    await self.patch(f"/contacts/{cid}/edit", json=payload)
                return {"success": True, "status": status, "action": "skipped", "email_address": email}

            # Restore archived contact
            if status == "archived":
                result = await self.patch(f"/contacts/{cid}/edit", json={**payload, "isPublished": True})
                if result["status"] not in (200, 201):
                    raise Exception(f"Mautic restore({email}): {result['status']} - {result['data']}")
                return {"success": True, "status": "subscribed", "action": "restored_from_archive", "email_address": email}

            # Normal update
            result = await self.patch(f"/contacts/{cid}/edit", json=payload)
            if result["status"] not in (200, 201):
                raise Exception(f"Mautic update({email}): {result['status']} - {result['data']}")
            return {"success": True, "status": status, "action": "updated", "email_address": email}

        # Create new contact
        result = await self.post("/contacts/new", json=payload)
        if result["status"] not in (200, 201):
            raise Exception(f"Mautic create({email}): {result['status']} - {result['data']}")
        new_id = result["data"].get("contact", {}).get("id")
        if new_id:
            self._id_cache[email.lower()] = int(new_id)
        return {"success": True, "status": "subscribed", "action": "created", "email_address": email}

    async def add_tags(self, email: str, tags: List[str]) -> Dict[str, Any]:
        """
        Add tags to a contact.

        Uses PATCH /api/contacts/{id}/edit with {"tags": ["tag1", "tag2"]}.
        Invalidates cache before lookup to handle newly-created contacts.
        """
        if not tags:
            return {"success": True, "tags_added": [], "email_address": email}

        # Invalidate cache - contact may have just been created this session
        self._invalidate_cache(email)
        contact_id = await self._get_contact_id(email)
        if contact_id is None:
            raise Exception(f"Cannot add tags to {email}: contact not found in Mautic")

        result = await self.patch(f"/contacts/{contact_id}/edit", json={"tags": tags})
        if result["status"] not in (200, 201):
            raise Exception(f"Mautic add_tags({email}): {result['status']} - {result['data']}")
        return {"success": True, "tags_added": tags, "email_address": email}

    async def remove_tags(self, email: str, tags: List[str]) -> Dict[str, Any]:
        """
        Remove tags from a contact.

        Uses PATCH /api/contacts/{id}/edit with {tags: ["-tag1", "-tag2"]}.
        Mautic removes tags that are prefixed with a minus sign.
        """
        if not tags:
            return {"success": True, "tags_removed": [], "email_address": email}

        contact_id = await self._get_contact_id(email)
        if contact_id is None:
            # Contact not found - tags already gone, treat as success
            logger.debug(f"remove_tags({email}): contact not found, skipping")
            return {"success": True, "tags_removed": tags, "email_address": email}

        remove_payload = [f"-{tag}" for tag in tags]
        result = await self.patch(f"/contacts/{contact_id}/edit", json={"tags": remove_payload})
        if result["status"] not in (200, 201):
            raise Exception(f"Mautic remove_tags({email}): {result['status']} - {result['data']}")
        return {"success": True, "tags_removed": tags, "email_address": email}

    async def unsubscribe_member(self, email: str) -> Dict[str, Any]:
        """Add email to Do Not Contact list (opt-out)."""
        existing = await self.get_member(email)
        if not existing["found"]:
            raise Exception(f"Cannot unsubscribe {email}: not found in Mautic")
        if existing["status"] == "unsubscribed":
            return {"success": True, "action": "already_unsubscribed", "email_address": email}
        cid = existing["mautic_id"]
        result = await self.post(f"/contacts/{cid}/dnc/{_DNC_CHANNEL}/add", json={"reason": _DNC_UNSUBSCRIBED})
        if result["status"] not in (200, 201):
            raise Exception(f"Mautic unsubscribe({email}): {result['status']} - {result['data']}")
        return {"success": True, "action": "unsubscribed", "email_address": email}

    async def archive_member(self, email: str) -> Dict[str, Any]:
        """Soft-delete a contact. 404 = already gone = success."""
        contact_id = await self._get_contact_id(email)
        if contact_id is None:
            return {"success": True, "action": "already_archived", "email_address": email}

        result = await self.delete(f"/contacts/{contact_id}/delete")
        self._invalidate_cache(email)

        if result["status"] in (200, 201, 204, 404):
            return {"success": True, "action": "archived", "email_address": email}
        raise Exception(f"Mautic archive({email}): {result['status']} - {result['data']}")

    async def get_all_members(self, count: int = _PAGE_SIZE, offset: int = 0) -> AsyncIterator[Dict[str, Any]]:
        """
        Async generator yielding all active Mautic contacts.

        Yields dicts with: email_address, status, tags, merge_fields
        """
        start = offset
        page_size = min(count, _PAGE_SIZE)

        while True:
            result = await self.get(
                "/contacts",
                params={"start": start, "limit": page_size, "orderBy": "id", "orderByDir": "asc"},
            )
            if result["status"] != 200:
                raise Exception(f"Mautic get_all_members: {result['status']} - {result['data']}")

            contacts = result["data"].get("contacts", {})
            if not contacts:
                break

            for contact in contacts.values():
                fields = contact.get("fields", {}).get("core", {})
                email_field = fields.get("email", {})
                email = (
                    email_field.get("value", "") if isinstance(email_field, dict)
                    else str(email_field or "")
                )
                if not email:
                    continue
                cid = contact.get("id")
                if cid:
                    self._id_cache[email.lower()] = int(cid)
                yield {
                    "email_address": email,
                    "status": self._derive_status(contact),
                    "tags": self._extract_tags(contact),
                    "merge_fields": {
                        "FNAME": self._get_core_field(fields, "firstname"),
                        "LNAME": self._get_core_field(fields, "lastname"),
                    },
                }

            start += len(contacts)
            if len(contacts) < page_size:
                break

    async def get_subscribed_count(self) -> int:
        """
        Return total contact count. Used by AudienceCapGuard for fresh-install detection.
        Also used as a proxy for subscribed count (Mautic has no direct endpoint).
        """
        result = await self.get("/contacts", params={"limit": 1, "start": 0})
        if result["status"] != 200:
            raise Exception(f"Mautic get_subscribed_count: {result['status']}")
        total = result["data"].get("total", 0)
        return int(total)
