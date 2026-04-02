"""
Teams webhook notifications for sync system alerts.

Sends Adaptive Card messages via Microsoft Teams incoming webhook.
"""

import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Webhook URL from environment (set in .env or GitHub secrets)
_WEBHOOK_URL: Optional[str] = None


def _get_webhook_url() -> Optional[str]:
    global _WEBHOOK_URL
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "")
    return _WEBHOOK_URL or None


async def send_teams_alert(title: str, message: str, facts: dict = None, color: str = "attention") -> bool:
    """
    Send an alert to Microsoft Teams via webhook.

    Args:
        title: Card title
        message: Main message body
        facts: Optional dict of key-value pairs to display
        color: Adaptive Card style - "attention" (red/yellow), "good" (green), "warning" (orange)

    Returns:
        True if sent successfully, False otherwise
    """
    url = _get_webhook_url()
    if not url:
        logger.warning("TEAMS_WEBHOOK_URL not set — skipping Teams notification")
        return False

    fact_items = []
    if facts:
        for k, v in facts.items():
            fact_items.append({"title": str(k), "value": str(v)})

    # Build Adaptive Card payload (Teams Workflows format)
    body_items = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": title,
            "wrap": True,
            "style": "heading",
        },
        {
            "type": "TextBlock",
            "text": message,
            "wrap": True,
        },
    ]

    if fact_items:
        body_items.append({
            "type": "FactSet",
            "facts": fact_items,
        })

    body_items.append({
        "type": "TextBlock",
        "text": f"_Sent at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "isSubtle": True,
        "size": "Small",
        "wrap": True,
    })

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body_items,
                },
            }
        ],
    }

    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 202):
                    logger.info(f"Teams alert sent: {title}")
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"Teams webhook failed ({resp.status}): {body}")
                    return False
    except Exception as e:
        logger.error(f"Teams webhook error: {e}")
        return False


async def notify_audience_cap_reached(
    current_count: int,
    cap: int,
    contacts_synced: int,
    contacts_skipped: int,
) -> bool:
    """Send a Teams alert when the Mailchimp audience cap is reached."""
    return await send_teams_alert(
        title="⚠️ Mailchimp Audience Cap Reached",
        message=(
            f"The sync has been **stopped** because the Mailchimp audience "
            f"has reached the hard cap of **{cap:,}** subscribed members."
        ),
        facts={
            "Current Subscribed": f"{current_count:,}",
            "Hard Cap": f"{cap:,}",
            "Remaining Slots": f"{max(0, cap - current_count):,}",
            "Contacts Synced This Run": f"{contacts_synced:,}",
            "Contacts Skipped (cap)": f"{contacts_skipped:,}",
        },
        color="attention",
    )


async def notify_audience_cap_warning(
    current_count: int,
    cap: int,
    remaining: int,
) -> bool:
    """Send a Teams warning when approaching the cap (< 50 slots left)."""
    return await send_teams_alert(
        title="🔶 Mailchimp Audience Approaching Cap",
        message=(
            f"Only **{remaining}** slots remaining before the hard cap of "
            f"**{cap:,}** is reached. The next sync may be partially or fully skipped."
        ),
        facts={
            "Current Subscribed": f"{current_count:,}",
            "Hard Cap": f"{cap:,}",
            "Remaining Slots": f"{remaining:,}",
        },
        color="warning",
    )
