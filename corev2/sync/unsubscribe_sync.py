"""
Mautic → HubSpot unsubscribe sync.

Scans Mautic for opted-out contacts and mirrors that status to HubSpot
using the Communication Preferences API.
"""

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
