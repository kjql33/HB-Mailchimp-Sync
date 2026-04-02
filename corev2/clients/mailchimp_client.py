"""
Mailchimp API Client for V2 Sync System.

Handles:
- Member upsert (never resubscribe unsubscribed/cleaned)
- Tag management (add/remove)
- Member archival (DELETE with 404=success)
- Structured responses (no raw HTTP leakage)
"""

import hashlib
from typing import Dict, List, Optional, Any
from .http_base import HTTPBaseClient


class MailchimpMemberStatus:
    """Mailchimp member status constants."""
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    CLEANED = "cleaned"
    PENDING = "pending"
    ARCHIVED = "archived"


class MailchimpClient(HTTPBaseClient):
    """Mailchimp API client with retry/rate-limit/circuit-breaker."""
    
    def __init__(
        self,
        api_key: str,
        server_prefix: str,
        audience_id: str,
        rate_limit: float = 10.0,  # Mailchimp allows 10 req/sec
        max_retries: int = 5
    ):
        """
        Initialize Mailchimp client.
        
        Args:
            api_key: Mailchimp API key
            server_prefix: Server prefix (e.g., "us1")
            audience_id: Mailchimp audience/list ID
            rate_limit: Max requests per second (default 10)
            max_retries: Max retry attempts (default 5)
        """
        base_url = f"https://{server_prefix}.api.mailchimp.com/3.0"
        
        # Mailchimp uses HTTP Basic auth: username can be anything, password is API key
        # https://mailchimp.com/developer/marketing/docs/fundamentals/#authentication
        import base64
        auth_string = base64.b64encode(f"anystring:{api_key}".encode()).decode()
        
        super().__init__(
            service_name="Mailchimp",
            base_url=base_url,
            rate_limit=rate_limit,
            max_retries=max_retries
        )
        self.audience_id = audience_id
        self.api_key = api_key
        self.default_headers = {
            "Authorization": f"Basic {auth_string}",
            "Content-Type": "application/json"
        }
    
    def _subscriber_hash(self, email: str) -> str:
        """Generate MD5 hash of lowercase email (Mailchimp's subscriber ID)."""
        return hashlib.md5(email.lower().encode()).hexdigest()
    
    async def get_audience_stats(self) -> Dict[str, Any]:
        """
        Get audience stats including subscribed member count.
        
        Returns:
            {
                "member_count": int,        # subscribed members
                "unsubscribe_count": int,
                "cleaned_count": int,
                "total_contacts": int        # all statuses combined
            }
        """
        endpoint = f"/lists/{self.audience_id}"
        result = await self.get(endpoint, params={"fields": "stats"})
        
        if result["status"] != 200:
            raise Exception(f"Mailchimp audience stats failed: {result['status']} - {result['data']}")
        
        stats = result["data"].get("stats", {})
        return {
            "member_count": stats.get("member_count", 0),
            "unsubscribe_count": stats.get("unsubscribe_count", 0),
            "cleaned_count": stats.get("cleaned_count", 0),
            "total_contacts": (
                stats.get("member_count", 0)
                + stats.get("unsubscribe_count", 0)
                + stats.get("cleaned_count", 0)
            ),
        }

    async def get_member(self, email: str) -> Dict[str, Any]:
        """
        Get member by email.
        
        Args:
            email: Member email address
        
        Returns:
            {
                "found": bool,
                "status": str | None,  # subscribed/unsubscribed/cleaned/pending
                "tags": List[str],
                "merge_fields": Dict[str, Any],
                "email_address": str
            }
        """
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}"
        
        result = await self.get(endpoint)
        
        if result["status"] == 404:
            return {
                "found": False,
                "status": None,
                "tags": [],
                "merge_fields": {},
                "email_address": email
            }
        
        if result["status"] != 200:
            raise Exception(f"Mailchimp API error: {result['status']} - {result['data']}")
        
        data = result["data"]
        return {
            "found": True,
            "status": data.get("status"),
            "tags": [tag["name"] for tag in data.get("tags", [])],
            "merge_fields": data.get("merge_fields", {}),
            "email_address": data.get("email_address", email)
        }
    
    async def upsert_member(
        self,
        email: str,
        merge_fields: Optional[Dict[str, Any]] = None,
        status_if_new: str = MailchimpMemberStatus.SUBSCRIBED
    ) -> Dict[str, Any]:
        """
        Upsert member (idempotent - never resubscribe unsubscribed/cleaned).
        
        Args:
            email: Member email address
            merge_fields: Custom fields to update
            status_if_new: Status for new members (default: subscribed)
        
        Returns:
            {
                "success": bool,
                "status": str,  # final member status
                "action": str,  # "created" | "updated" | "skipped"
                "email_address": str
            }
        """
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}"
        
        # Check existing status first
        existing = await self.get_member(email)
        
        # INV-005: NEVER resubscribe unsubscribed/cleaned (user opted out)
        # BUT: ARCHIVED members should be RESTORED if back in active HubSpot list
        if existing["found"] and existing["status"] in [
            MailchimpMemberStatus.UNSUBSCRIBED,
            MailchimpMemberStatus.CLEANED
        ]:
            # Only update merge_fields, preserve unsubscribed/cleaned status
            if merge_fields:
                payload = {"merge_fields": merge_fields}
                result = await self.patch(endpoint, json=payload)
                
                if result["status"] == 200:
                    return {
                        "success": True,
                        "status": existing["status"],
                        "action": "updated",
                        "email_address": email
                    }
                else:
                    raise Exception(f"Mailchimp PATCH failed: {result['status']} - {result['data']}")
            else:
                return {
                    "success": True,
                    "status": existing["status"],
                    "action": "skipped",
                    "email_address": email
                }
        
        # ARCHIVED members: Restore them (they're back in active HubSpot list)
        if existing["found"] and existing["status"] == MailchimpMemberStatus.ARCHIVED:
            # Use PUT with explicit status to restore from archive
            payload = {
                "email_address": email,
                "status": status_if_new  # Restore to subscribed
            }
            if merge_fields:
                payload["merge_fields"] = merge_fields
            
            result = await self.put(endpoint, json=payload)
            
            if result["status"] == 200:
                return {
                    "success": True,
                    "status": status_if_new,
                    "action": "restored_from_archive",
                    "email_address": email
                }
            else:
                raise Exception(f"Mailchimp restore failed: {result['status']} - {result['data']}")
        
        # Create or update with PUT (Mailchimp's upsert endpoint)
        payload = {
            "email_address": email,
            "status_if_new": status_if_new
        }
        if merge_fields:
            payload["merge_fields"] = merge_fields
        
        result = await self.put(endpoint, json=payload)
        
        if result["status"] == 200:
            data = result["data"]
            action = "created" if not existing["found"] else "updated"
            return {
                "success": True,
                "status": data.get("status"),
                "action": action,
                "email_address": email
            }
        else:
            raise Exception(f"Mailchimp PUT failed: {result['status']} - {result['data']}")
    
    async def add_tags(self, email: str, tags: List[str]) -> Dict[str, Any]:
        """
        Add tags to member (idempotent).
        
        Args:
            email: Member email address
            tags: List of tag names to add
        
        Returns:
            {
                "success": bool,
                "tags_added": List[str],
                "email_address": str
            }
        """
        if not tags:
            return {"success": True, "tags_added": [], "email_address": email}
        
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}/tags"
        
        payload = {
            "tags": [{"name": tag, "status": "active"} for tag in tags]
        }
        
        result = await self.post(endpoint, json=payload)
        
        if result["status"] == 204:
            return {
                "success": True,
                "tags_added": tags,
                "email_address": email
            }
        else:
            raise Exception(f"Mailchimp tag add failed: {result['status']} - {result['data']}")
    
    async def remove_tags(self, email: str, tags: List[str]) -> Dict[str, Any]:
        """
        Remove tags from member (idempotent).
        
        Args:
            email: Member email address
            tags: List of tag names to remove
        
        Returns:
            {
                "success": bool,
                "tags_removed": List[str],
                "email_address": str
            }
        """
        if not tags:
            return {"success": True, "tags_removed": [], "email_address": email}
        
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}/tags"
        
        payload = {
            "tags": [{"name": tag, "status": "inactive"} for tag in tags]
        }
        
        result = await self.post(endpoint, json=payload)
        
        if result["status"] == 204:
            return {
                "success": True,
                "tags_removed": tags,
                "email_address": email
            }
        else:
            raise Exception(f"Mailchimp tag remove failed: {result['status']} - {result['data']}")
    
    async def unsubscribe_member(self, email: str) -> Dict[str, Any]:
        """
        Unsubscribe member (set status=unsubscribed).
        
        Note: This is different from archive. Unsubscribed members remain in the audience
        but with status='unsubscribed'. They can later be archived.
        
        Args:
            email: Member email address
        
        Returns:
            {
                "success": bool,
                "action": str,  # "unsubscribed" | "already_unsubscribed"
                "email_address": str,
                "status": str
            }
        """
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}"
        
        # Update status to unsubscribed
        result = await self.patch(endpoint, json={"status": "unsubscribed"})
        
        if result["status"] in [200, 204]:
            return {
                "success": True,
                "action": "unsubscribed",
                "email_address": email,
                "status": result.get("data", {}).get("status", "unsubscribed")
            }
        else:
            raise Exception(f"Mailchimp unsubscribe failed: {result['status']} - {result['data']}")
    
    async def archive_member(self, email: str) -> Dict[str, Any]:
        """
        Archive member (DELETE - 404 treated as success).
        
        Args:
            email: Member email address
        
        Returns:
            {
                "success": bool,
                "action": str,  # "archived" | "already_archived"
                "email_address": str
            }
        """
        subscriber_hash = self._subscriber_hash(email)
        endpoint = f"/lists/{self.audience_id}/members/{subscriber_hash}"
        
        result = await self.delete(endpoint)
        
        if result["status"] == 204:
            return {
                "success": True,
                "action": "archived",
                "email_address": email
            }
        elif result["status"] == 404:
            # 404 = already archived or never existed (both are success states)
            return {
                "success": True,
                "action": "already_archived",
                "email_address": email
            }
        else:
            raise Exception(f"Mailchimp archive failed: {result['status']} - {result['data']}")
    
    async def get_all_members(self, count: int = 1000, offset: int = 0):
        """
        Iterate over all Mailchimp audience members (paginated).
        
        Args:
            count: Members per page (max 1000)
            offset: Starting offset
        
        Yields:
            Member dicts with email_address, status, tags, merge_fields
        """
        while True:
            endpoint = f"/lists/{self.audience_id}/members"
            params = {"count": min(count, 1000), "offset": offset}
            
            result = await self.get(endpoint, params=params)
            
            if result["status"] != 200:
                raise Exception(f"Mailchimp list members failed: {result['status']}")
            
            members = result["data"].get("members", [])
            
            if not members:
                break  # No more members
            
            for member in members:
                # Extract tags from tags array
                tags = [tag["name"] for tag in member.get("tags", [])]
                yield {
                    "email_address": member.get("email_address"),
                    "status": member.get("status"),
                    "tags": tags,
                    "merge_fields": member.get("merge_fields", {}),
                }
            
            offset += len(members)
            
            # If we got fewer than requested, we're done
            if len(members) < count:
                break
