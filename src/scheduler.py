"""APScheduler configuration for HubSpot→Sheet sync.

Two cron jobs: 04:30 UTC and 16:30 UTC (= 10:00 AM IST and 10:00 PM IST).

Each job:
  1. Runs run_sync()
  2. Appends RunResult to data/run_history.json (keep last 100)
  3. Sends Slack notification

SCHEDULER_ENABLED env var (default false) — if false, scheduler is not started.
Set max_instances=1 per job to prevent overlap.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.sync import RunResult, run_sync
from src import slack

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "run_history.json"
_MAX_HISTORY = 100

# Singleton scheduler instance
_scheduler: BackgroundScheduler | None = None


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _result_to_dict(result: RunResult, run_type: str = "SYNC") -> dict:
    """Serialise a RunResult to a JSON-friendly dict."""
    duration_s = round((result.finished_at - result.started_at).total_seconds())
    return {
        "run_type": run_type,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "duration_s": f"{duration_s}s",
        "rows_total": result.rows_total,
        "rows_synced": result.rows_synced,
        "rows_skipped": result.rows_skipped,
        "errors": result.errors,
        "misses": result.misses,
    }


def append_run_history(result: RunResult, run_type: str = "SYNC") -> None:
    """Append RunResult to data/run_history.json, keeping last 100 entries."""
    _ensure_data_dir()

    history: list[dict] = []
    if _HISTORY_FILE.exists():
        try:
            with open(_HISTORY_FILE) as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read run history: %s — starting fresh", exc)
            history = []

    history.append(_result_to_dict(result, run_type=run_type))
    # Trim to last 100
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]

    try:
        with open(_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except OSError as exc:
        logger.error("Failed to write run history: %s", exc)


def load_run_history(limit: int = 20) -> list[dict]:
    """Return the last `limit` run history entries, newest first."""
    if not _HISTORY_FILE.exists():
        return []
    try:
        with open(_HISTORY_FILE) as f:
            history: list[dict] = json.load(f)
        return list(reversed(history[-limit:]))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read run history: %s", exc)
        return []


def get_last_result() -> dict | None:
    """Return the most recent RunResult dict, or None."""
    history = load_run_history(limit=1)
    return history[0] if history else None


def _sync_job() -> None:
    """The function executed by each scheduled cron job."""
    logger.info("Scheduled sync starting...")
    try:
        result = run_sync(dry_run=False)
        append_run_history(result, run_type="SYNC")
        slack.notify_success(result)
    except Exception as exc:
        logger.error("Scheduled sync failed: %s", exc, exc_info=True)
        error_msg = str(exc)
        slack.notify_error(error_msg)
        # Still record a minimal failure entry
        now = datetime.utcnow()
        failed_result = RunResult(
            started_at=now,
            finished_at=now,
            rows_total=0,
            rows_synced=0,
            rows_skipped=0,
            errors=[error_msg],
            misses=[],
        )
        append_run_history(failed_result, run_type="SYNC")


def get_scheduler() -> BackgroundScheduler | None:
    """Return the global scheduler instance (may be None if not started)."""
    return _scheduler


def start_scheduler() -> None:
    """Start the APScheduler background scheduler (if SCHEDULER_ENABLED=true)."""
    global _scheduler

    enabled = os.getenv("SCHEDULER_ENABLED", "false").strip().lower() == "true"
    if not enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED != true)")
        return

    _ensure_data_dir()

    _scheduler = BackgroundScheduler(timezone="UTC")

    # 04:30 UTC = 10:00 AM IST
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=4, minute=30, timezone="UTC"),
        id="sync_morning",
        name="HubSpot Sync — Morning (04:30 UTC)",
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 16:30 UTC = 10:00 PM IST
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=16, minute=30, timezone="UTC"),
        id="sync_evening",
        name="HubSpot Sync — Evening (16:30 UTC)",
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.start()
    logger.info("Scheduler started — jobs: sync_morning (04:30 UTC), sync_evening (16:30 UTC)")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler on app shutdown."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception as exc:
            logger.warning("Error stopping scheduler: %s", exc)
        _scheduler = None


def get_next_run_times() -> list[str]:
    """Return a list of next run times (ISO strings) for scheduled jobs."""
    if _scheduler is None:
        return []
    times = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        if next_run:
            times.append(next_run.isoformat())
    return sorted(times)
