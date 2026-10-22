"""Snapshot of what the sync last left in the sheet's Next Steps / Due Date cells, per domain.

{domain: {"deal_id", "next_steps", "due_date", "synced_at"}}. A sheet cell that differs from this is an AE edit;
a HubSpot value that differs is a HubSpot edit. Missing or corrupt file = empty snapshot = every row is
re-baselined on the next run (nothing is pushed on a baseline run).
"""
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).parent.parent / "data" / "next_steps_state.json"


def load(path: Path | str = DEFAULT_PATH) -> dict:
    """Read the snapshot; {} if it is missing, unreadable or not a JSON object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Next-steps snapshot unreadable (%s) — starting from an empty baseline", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Next-steps snapshot is not a JSON object — starting from an empty baseline")
        return {}
    return data


def save(snapshot: dict, path: Path | str = DEFAULT_PATH) -> None:
    """Write atomically (temp file + rename) so a crash never leaves a half-written snapshot."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".next_steps_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
