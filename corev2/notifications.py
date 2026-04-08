"""
Microsoft Teams webhook notifications.

Sends Adaptive Card alerts for:
- Audience cap reached (blocks sync)
- Audience cap warning (< 50 slots remaining)
- Sync failures
"""

import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


async def send_teams_alert(
    webhook_url: str,
    title: str,
    message: str,
    color: str = "attention",
    facts: Optional[list] = None,
) -> bool:
    """
    Send an Adaptive Card alert to Microsoft Teams.

    Args:
        webhook_url: Teams incoming webhook URL
        title:       Card title
        message:     Card body text
        color:       "attention" (red), "warning" (yellow), "good" (green)
        facts:       Optional list of {"title": str, "value": str} dicts

    Returns:
        True if sent successfully, False otherwise
    """
    if not webhook_url:
        logger.warning("Teams webhook URL not configured - skipping notification")
        return False

    body: dict = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": color,
                        },
                        {
                            "type": "TextBlock",
                            "text": message,
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }

    if facts:
        body["attachments"][0]["content"]["body"].append(
            {
                "type": "FactSet",
                "facts": [{"title": f["title"], "value": str(f["value"])} for f in facts],
            }
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (200, 202):
                    logger.info(f"Teams alert sent: {title}")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Teams alert failed ({resp.status}): {text[:200]}")
                    return False
    except Exception as e:
        logger.warning(f"Teams notification error: {e}")
        return False


async def send_audience_cap_reached(
    webhook_url: str,
    current_count: int,
    cap: int,
    skipped: int,
) -> bool:
    """Send alert when audience cap is hit."""
    return await send_teams_alert(
        webhook_url=webhook_url,
        title="Mautic Audience Cap Reached - Sync Blocked",
        message="The Mautic audience has reached its configured cap. New contacts are being skipped.",
        color="attention",
        facts=[
            {"title": "Current count", "value": current_count},
            {"title": "Cap limit", "value": cap},
            {"title": "Contacts skipped this run", "value": skipped},
        ],
    )


async def send_audience_cap_warning(
    webhook_url: str,
    current_count: int,
    cap: int,
    remaining: int,
) -> bool:
    """Send warning when approaching audience cap."""
    return await send_teams_alert(
        webhook_url=webhook_url,
        title="Mautic Audience Cap Warning",
        message=f"Only {remaining} subscriber slots remaining before the cap is reached.",
        color="warning",
        facts=[
            {"title": "Current count", "value": current_count},
            {"title": "Cap limit", "value": cap},
            {"title": "Remaining slots", "value": remaining},
        ],
    )
