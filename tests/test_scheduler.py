"""Scheduler configuration: the sync runs 4x a day - 01:30, 10:00, 13:30 and 22:00 IST (20:00, 04:30, 08:00, 16:30 UTC)."""

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


def test_single_four_daily_job_is_registered(started_scheduler):
    jobs = started_scheduler.get_jobs()
    assert [j.id for j in jobs] == ["sync_four_daily"]


def test_job_fires_at_the_four_ist_times(started_scheduler):
    job = started_scheduler.get_job("sync_four_daily")
    start = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)
    got = _fire_times(job, start, 8)
    # 10:00 IST, 13:30 IST, 22:00 IST, 01:30 IST (next day) - in UTC
    assert [(t.hour, t.minute) for t in got] == [(4, 30), (8, 0), (16, 30), (20, 0)] * 2
    assert got[0].date().isoformat() == "2026-10-22"
    assert got[4].date().isoformat() == "2026-10-23"


def test_job_cannot_overlap(started_scheduler):
    assert started_scheduler.get_job("sync_four_daily").max_instances == 1


def test_get_next_run_times_lists_the_next_four_runs(started_scheduler):
    got = scheduler.get_next_run_times(now=datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc))
    assert len(got) == 4
    parsed = [datetime.fromisoformat(t) for t in got]
    assert [(t.hour, t.minute) for t in parsed] == [(4, 30), (8, 0), (16, 30), (20, 0)]
    assert parsed == sorted(parsed)


def test_get_next_run_times_is_empty_when_scheduler_is_off():
    scheduler._scheduler = None
    assert scheduler.get_next_run_times() == []


def test_disabled_scheduler_registers_nothing(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    scheduler._scheduler = None
    scheduler.start_scheduler()
    assert scheduler.get_scheduler() is None
