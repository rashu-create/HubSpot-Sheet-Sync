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
