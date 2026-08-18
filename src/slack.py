"""Slack notifications for HubSpot→Sheet sync results.

Uses SLACK_BOT_TOKEN + SLACK_ALERT_CHANNEL from env.
If either is not set, notifications are skipped silently.
"""

import logging
import os

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.sync import RunResult

logger = logging.getLogger(__name__)


def _get_client() -> tuple[WebClient, str] | tuple[None, None]:
    """Return (WebClient, channel_id) or (None, None) if not configured."""
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel = os.getenv("SLACK_ALERT_CHANNEL", "").strip()
    if not token or not channel:
        logger.warning("SLACK_BOT_TOKEN or SLACK_ALERT_CHANNEL not set — Slack notifications disabled")
        return None, None
    return WebClient(token=token), channel


def notify_success(result: RunResult) -> None:
    """Send a success notification with sync summary."""
    client, channel = _get_client()
    if client is None:
        return

    lines = [
        f"✅ HubSpot→Sheet sync done: {result.rows_synced} rows updated, "
        f"{result.rows_skipped} skipped (no HubSpot match)"
    ]

    if result.misses:
        preview = result.misses[:5]
        lines.append(f"Domains with no HubSpot company ({len(result.misses)} total):")
        for domain in preview:
            lines.append(f"  • {domain}")
        if len(result.misses) > 5:
            lines.append(f"  … and {len(result.misses) - 5} more")

    if result.errors:
        lines.append(f"⚠️ {len(result.errors)} error(s) during sync")

    try:
        client.chat_postMessage(channel=channel, text="\n".join(lines))
    except SlackApiError as exc:
        logger.warning("Failed to send Slack success notification: %s", exc)


def notify_error(error: str) -> None:
    """Send an error notification."""
    client, channel = _get_client()
    if client is None:
        return

    try:
        client.chat_postMessage(channel=channel, text=f"❌ HubSpot→Sheet sync failed: {error}")
    except SlackApiError as exc:
        logger.warning("Failed to send Slack error notification: %s", exc)
