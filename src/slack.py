"""Slack notifications for HubSpot→Sheet sync results.

Uses SLACK_WEBHOOK_URL from env. If not set, skips silently.
Uses slack_sdk WebhookClient to POST to the incoming webhook.
"""

import logging
import os

from slack_sdk.webhook import WebhookClient

from src.sync import RunResult

logger = logging.getLogger(__name__)


def _get_client() -> WebhookClient | None:
    """Return a WebhookClient or None if SLACK_WEBHOOK_URL is not configured."""
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        logger.warning("SLACK_WEBHOOK_URL not set — Slack notifications disabled")
        return None
    return WebhookClient(url)


def notify_success(result: RunResult) -> None:
    """Send a success notification with sync summary."""
    client = _get_client()
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

    message = "\n".join(lines)
    try:
        resp = client.send(text=message)
        if resp.status_code != 200:
            logger.warning("Slack webhook returned HTTP %s: %s", resp.status_code, resp.body)
    except Exception as exc:
        logger.warning("Failed to send Slack success notification: %s", exc)


def notify_error(error: str) -> None:
    """Send an error notification."""
    client = _get_client()
    if client is None:
        return

    message = f"❌ HubSpot→Sheet sync failed: {error}"
    try:
        resp = client.send(text=message)
        if resp.status_code != 200:
            logger.warning("Slack webhook returned HTTP %s: %s", resp.status_code, resp.body)
    except Exception as exc:
        logger.warning("Failed to send Slack error notification: %s", exc)
