"""Slack summary and run-history record carry the Source of meeting numbers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from src import scheduler, slack
from src.sync import RunResult


def _result(**kw):
    base = dict(started_at=datetime(2026, 10, 22, 4, 30), finished_at=datetime(2026, 10, 22, 4, 34),
                rows_total=456, rows_synced=424, rows_skipped=6)
    base.update(kw)
    return RunResult(**base)


def _posted_text(result):
    client = MagicMock()
    with patch.object(slack, "_get_client", return_value=(client, "C123")):
        slack.notify_success(result)
    return client.chat_postMessage.call_args.kwargs["text"]


def test_success_message_has_the_source_line():
    text = _posted_text(_result(source_filled=440, source_analysis=400, source_legacy=40,
                                source_unresolved=16))
    assert "Source of meeting: 440 filled (400 analysis, 40 legacy), 16 unresolved" in text


def test_success_message_says_when_the_column_was_skipped():
    text = _posted_text(_result(source_skipped=True, errors=["Source of meeting: analysis read failed — x"]))
    assert "Source of meeting: skipped this run" in text


def test_run_history_record_has_the_source_numbers():
    d = scheduler._result_to_dict(_result(source_filled=440, source_analysis=400, source_legacy=40,
                                          source_unresolved=16, source_skipped=False))
    assert (d["source_filled"], d["source_analysis"], d["source_legacy"], d["source_unresolved"],
            d["source_skipped"]) == (440, 400, 40, 16, False)


# ── Next Steps / Due Date write-back line ────────────────────────────────────

def test_no_next_steps_line_when_the_feature_is_off():
    assert "Next steps" not in _posted_text(_result())


def test_dry_run_line_says_nothing_was_sent():
    text = _posted_text(_result(ns_mode="dry_run", ns_would_update=5, ns_conflicts=1, ns_blocked=2))
    assert "Next steps → HubSpot (DRY RUN, nothing sent): 5 would update, 1 conflict, 2 blocked" in text
    assert "log tab" in text


def test_live_line_counts_updates_conflicts_blocked_and_failures():
    text = _posted_text(_result(ns_mode="on", ns_updated=4, ns_conflicts=1, ns_blocked=0, ns_errors=2))
    assert "Next steps → HubSpot: 4 updated, 1 conflict, 0 blocked, 2 FAILED" in text


def test_live_line_without_failures_has_no_failed_part():
    text = _posted_text(_result(ns_mode="on", ns_updated=1))
    assert "FAILED" not in text and "Next steps → HubSpot: 1 updated, 0 conflicts, 0 blocked" in text


def test_run_history_has_the_next_steps_numbers():
    d = scheduler._result_to_dict(_result(ns_mode="on", ns_updated=4, ns_would_update=0, ns_conflicts=1,
                                          ns_blocked=2, ns_errors=3))
    assert (d["ns_mode"], d["ns_updated"], d["ns_would_update"], d["ns_conflicts"], d["ns_blocked"],
            d["ns_errors"]) == ("on", 4, 0, 1, 2, 3)
