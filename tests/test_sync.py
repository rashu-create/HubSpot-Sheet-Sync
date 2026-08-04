"""Tests for sync.py orchestration logic."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.sync import RunResult, run_sync


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_row_data(domain: str) -> dict:
    """Return a minimal row_data dict as hubspot.get_row_data would return."""
    return {
        "C": "Yes",
        "D": "Alice Smith",
        "E": "High",
        "T": "Qualified",
        "AF": "",  # SDR injected by sync
    }


# ── dry_run=True does not call write_pipeline_rows ────────────────────────────

class TestDryRun:
    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_dry_run_does_not_write(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """dry_run=True must never call write_pipeline_rows."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [(2, "example.com"), (3, "acme.com")]
        mock_get_row_data.return_value = _make_row_data("example.com")

        result = run_sync(dry_run=True)

        mock_write_rows.assert_not_called()
        assert result.rows_total == 2

    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_normal_run_calls_write(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """dry_run=False must call write_pipeline_rows when rows are available."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [(2, "example.com")]
        mock_get_row_data.return_value = _make_row_data("example.com")

        result = run_sync(dry_run=False)

        mock_write_rows.assert_called_once()
        assert result.rows_synced == 1


# ── Miss counting ─────────────────────────────────────────────────────────────

class TestMissCounting:
    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_none_from_hubspot_is_a_miss(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """When get_row_data returns None, domain goes to misses and is not written."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [
            (2, "found.com"),
            (3, "notfound.com"),
            (4, "alsonotfound.com"),
        ]

        def side_effect(domain):
            if domain == "found.com":
                return _make_row_data(domain)
            return None

        mock_get_row_data.side_effect = side_effect

        result = run_sync(dry_run=True)

        assert result.rows_total == 3
        assert result.rows_synced == 1
        assert result.rows_skipped == 2
        assert "notfound.com" in result.misses
        assert "alsonotfound.com" in result.misses
        assert "found.com" not in result.misses

    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_all_misses(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """All misses — rows_synced=0, write is not called even in normal mode."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [(2, "x.com"), (3, "y.com")]
        mock_get_row_data.return_value = None

        result = run_sync(dry_run=False)

        assert result.rows_synced == 0
        assert result.rows_skipped == 2
        assert len(result.misses) == 2
        mock_write_rows.assert_not_called()


# ── RunResult fields ──────────────────────────────────────────────────────────

class TestRunResultFields:
    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_result_timestamps(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """RunResult must have started_at <= finished_at (both datetime objects)."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = []
        mock_get_row_data.return_value = None

        result = run_sync(dry_run=True)

        assert isinstance(result.started_at, datetime)
        assert isinstance(result.finished_at, datetime)
        assert result.started_at <= result.finished_at

    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_result_has_errors_list(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """errors field is always a list (even if empty)."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = []

        result = run_sync(dry_run=True)

        assert isinstance(result.errors, list)
        assert isinstance(result.misses, list)

    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_sdr_injected_into_af_column(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """SDR value from sdr_map should be injected as column AF in each update."""
        mock_build_sdr_map.return_value = {"example.com": "Sarah SDR"}
        mock_read_domains.return_value = [(2, "example.com")]
        mock_get_row_data.return_value = {"C": "Yes", "AF": ""}

        # Capture what write_pipeline_rows receives
        captured = []
        mock_write_rows.side_effect = lambda updates: captured.extend(updates)

        run_sync(dry_run=False)

        assert captured, "Expected at least one update"
        row_update = captured[0]
        assert row_update["values"].get("AF") == "Sarah SDR"

    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_sdr_injected_empty_when_no_match(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """SDR column AF should be empty string when domain not in sdr_map."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [(2, "example.com")]
        mock_get_row_data.return_value = {"C": "Yes", "AF": ""}

        captured = []
        mock_write_rows.side_effect = lambda updates: captured.extend(updates)

        run_sync(dry_run=False)

        assert captured
        assert captured[0]["values"].get("AF") == ""


# ── Sheet read failure ────────────────────────────────────────────────────────

class TestSheetReadFailure:
    @patch("src.sync.sheets.write_pipeline_rows")
    @patch("src.sync.sheets.read_pipeline_domains")
    @patch("src.sync.sheets.build_sdr_map")
    @patch("src.sync.hubspot.get_row_data")
    def test_domain_read_failure_returns_error_result(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """If read_pipeline_domains raises, RunResult should capture the error."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.side_effect = RuntimeError("Sheet not found")

        result = run_sync(dry_run=True)

        assert result.rows_total == 0
        assert len(result.errors) > 0
        assert any("Pipeline domain read failed" in e for e in result.errors)
