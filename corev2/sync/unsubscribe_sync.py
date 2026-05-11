"""
Mautic → HubSpot unsubscribe sync.

Scans Mautic for opted-out contacts and mirrors that status to HubSpot
using the Communication Preferences API.

Step 1:  scan_and_sync()         — Mautic unsubscribed → HubSpot opt-out
Step 1B: scan_cleaned_and_sync() — Mautic bounced/cleaned → strip tags + set hs_email_bad_address
"""

import asyncio
import logging
from typing import Dict, Any

from corev2.clients.hubspot_client import HubSpotClient
from corev2.clients.mautic_client import MauticClient
from corev2.config.schema import V2Config

logger = logging.getLogger(__name__)


class UnsubscribeSyncEngine:
    """Syncs Mautic unsubscribes to HubSpot."""

    def __init__(self, config: V2Config, hs_client: HubSpotClient, mc_client: MauticClient):
        self.config = config
        self.hs_client = hs_client
        self.mc_client = mc_client

    async def scan_and_sync(self) -> Dict[str, Any]:
        """
        Scan Mautic for unsubscribed contacts and opt them out in HubSpot.

        Returns:
            {mailchimp_unsubscribed, hubspot_updates, skipped, errors}
        """
        logger.info("Starting Mautic → HubSpot unsubscribe sync...")

        summary = {
            "mailchimp_unsubscribed": 0,
            "hubspot_updates": 0,
            "skipped": 0,
            "errors": [],
        }

        unsubscribed = []
        async for member in self.mc_client.get_all_members():
            if member.get("status") == "unsubscribed":
                unsubscribed.append(member.get("email_address"))

        summary["mailchimp_unsubscribed"] = len(unsubscribed)
        logger.info(f"Found {len(unsubscribed)} unsubscribed contacts in Mautic")

        if not unsubscribed:
            logger.info("No unsubscribes to sync")
            return summary

        for email in unsubscribed:
            try:
                hs_contact = await self.hs_client.get_contact_by_email(email)
                if not hs_contact["found"]:
                    logger.debug(f"  {email}: not found in HubSpot - skipping")
                    summary["skipped"] += 1
                    continue

                # Check current HubSpot subscription status
                sub_result = await self.hs_client.get(
                    f"/communication-preferences/v3/status/email/{email}"
                )
                if sub_result["status"] != 200:
                    summary["skipped"] += 1
                    continue

                subscriptions = sub_result["data"].get("subscriptionStatuses", [])
                already_out = all(
                    s.get("status") in ("NOT_SUBSCRIBED", "OPT_OUT") for s in subscriptions
                )
                if already_out:
                    summary["skipped"] += 1
                    continue

                # Unsubscribe from all active subscription types
                updated = 0
                for sub in subscriptions:
                    if sub.get("status") in ("NOT_SUBSCRIBED", "OPT_OUT"):
                        continue
                    try:
                        result = await self.hs_client.post(
                            "/communication-preferences/v3/unsubscribe",
                            json={
                                "emailAddress": email,
                                "subscriptionId": str(sub["id"]),
                                "legalBasis": "LEGITIMATE_INTEREST_OTHER",
                                "legalBasisExplanation": "Contact unsubscribed in Mautic",
                            },
                        )
                        if result["status"] in (200, 201, 204):
                            updated += 1
                    except Exception as e:
                        if "already" not in str(e).lower():
                            logger.warning(f"  {email}: failed to unsubscribe from {sub.get('name')}: {e}")

                if updated > 0:
                    logger.info(f"  {email}: unsubscribed from {updated} HubSpot subscriptions")
                    summary["hubspot_updates"] += 1
                else:
                    summary["skipped"] += 1

            except Exception as e:
                logger.error(f"  {email}: error - {e}")
                summary["errors"].append({"email": email, "error": str(e)})

        logger.info(
            f"Unsubscribe sync complete: "
            f"{summary['hubspot_updates']} updated, "
            f"{summary['skipped']} skipped, "
            f"{len(summary['errors'])} errors"
        )
        return summary

    async def scan_cleaned_and_sync(self) -> Dict[str, Any]:
        """
        Scan Mautic for hard-bounced (cleaned) contacts and flag them in HubSpot.

        For each cleaned contact:
          1. Remove all tags from Mautic (clean slate — prevents re-tagging next run)
          2. Set hs_email_bad_address=true in HubSpot

        Returns:
            {mautic_cleaned, tags_removed, hubspot_flagged, not_in_hubspot, errors}
        """
        logger.info("Starting Mautic → HubSpot cleaned/bounced contact sync (Step 1B)...")

        summary: Dict[str, Any] = {
            "mautic_cleaned": 0,
            "tags_removed": 0,
            "hubspot_flagged": 0,
            "not_in_hubspot": 0,
            "errors": [],
        }

        cleaned = []
        async for member in self.mc_client.get_all_members():
            if member.get("status") == "cleaned":
                cleaned.append(member)

        summary["mautic_cleaned"] = len(cleaned)
        logger.info(f"Found {len(cleaned)} cleaned/bounced contacts in Mautic")

        if not cleaned:
            logger.info("No cleaned contacts to process")
            return summary

        for member in cleaned:
            email = member.get("email_address")
            tags = member.get("tags", [])
            try:
                # Step 1: Strip all Mautic tags so they won't be re-applied next sync run
                if tags:
                    remove_result = await self.mc_client.remove_tags(email, tags)
                    if remove_result.get("success"):
                        summary["tags_removed"] += len(tags)
                        logger.debug(f"  {email}: removed {len(tags)} tags from Mautic")

                # Step 2: Flag hs_email_bad_address in HubSpot
                hs_contact = await self.hs_client.get_contact_by_email(email)
                if not hs_contact["found"]:
                    logger.debug(f"  {email}: not found in HubSpot — skipping flag")
                    summary["not_in_hubspot"] += 1
                    continue

                await self.hs_client.update_contact_property(
                    hs_contact["vid"], "hs_email_bad_address", "true"
                )
                summary["hubspot_flagged"] += 1
                logger.info(f"  {email}: flagged hs_email_bad_address=true in HubSpot")

            except Exception as e:
                logger.error(f"  {email}: error during cleaned sync — {e}")
                summary["errors"].append({"email": email, "error": str(e)})

            await asyncio.sleep(0.1)  # gentle rate limiting

        logger.info(
            f"Cleaned sync complete: {summary['mautic_cleaned']} found, "
            f"{summary['tags_removed']} tags removed, "
            f"{summary['hubspot_flagged']} HubSpot flagged, "
            f"{summary['not_in_hubspot']} not in HubSpot, "
            f"{len(summary['errors'])} errors"
        )
        return summary
