"""Scheduler configuration: the sync runs at 10:00 and 22:00 IST (04:30 and 16:30 UTC)."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src import scheduler


@pytest.fixture
def started_scheduler(monkeypatch):
    """start_scheduler() with the real trigger config but without actually running the loop."""
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    with patch("apscheduler.schedulers.background.BackgroundScheduler.start"):
        scheduler.start_scheduler()
        yield scheduler.get_scheduler()
    scheduler._scheduler = None


def _fire_times(job, start, count):
    """Return the next `count` fire times of a job after `start`."""
    times, prev = [], None
    now = start
    for _ in range(count):
        nxt = job.trigger.get_next_fire_time(prev, now)
        times.append(nxt)
        prev, now = nxt, nxt
    return times


def test_single_twice_daily_job_is_registered(started_scheduler):
    jobs = started_scheduler.get_jobs()
    assert [j.id for j in jobs] == ["sync_twice_daily"]


def test_job_fires_at_0430_and_1630_utc(started_scheduler):
    job = started_scheduler.get_job("sync_twice_daily")
    start = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)
    got = _fire_times(job, start, 4)
    assert [(t.hour, t.minute) for t in got] == [(4, 30), (16, 30), (4, 30), (16, 30)]
    assert got[0].date().isoformat() == "2026-10-22"
    assert got[2].date().isoformat() == "2026-10-23"


def test_job_cannot_overlap(started_scheduler):
    assert started_scheduler.get_job("sync_twice_daily").max_instances == 1


def test_disabled_scheduler_registers_nothing(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler.get_scheduler() is None
