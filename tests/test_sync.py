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
    def test_sdr_injected_into_ag_column(
        self,
        mock_get_row_data,
        mock_build_sdr_map,
        mock_read_domains,
        mock_write_rows,
    ):
        """SDR value from sdr_map should be injected as column AG in each update."""
        mock_build_sdr_map.return_value = {"example.com": "Sarah SDR"}
        mock_read_domains.return_value = [(2, "example.com")]
        mock_get_row_data.return_value = {"C": "Yes", "AG": ""}

        # Capture what write_pipeline_rows receives
        captured = []
        mock_write_rows.side_effect = lambda updates: captured.extend(updates)

        run_sync(dry_run=False)

        assert captured, "Expected at least one update"
        row_update = captured[0]
        assert row_update["values"].get("AG") == "Sarah SDR"

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
        """SDR column AG should be empty string when domain not in sdr_map."""
        mock_build_sdr_map.return_value = {}
        mock_read_domains.return_value = [(2, "example.com")]
        mock_get_row_data.return_value = {"C": "Yes", "AG": ""}

        captured = []
        mock_write_rows.side_effect = lambda updates: captured.extend(updates)

        run_sync(dry_run=False)

        assert captured
        assert captured[0]["values"].get("AG") == ""


# ── Employee count merge + ICP size helpers ───────────────────────────────────

class TestEmployeeCountMerge:
    """_compute_employee_count_merged and _compute_icp_size behave correctly."""

    def _props(self, employee_count=None, numberofemployees=None, sales_team="0"):
        return {
            "employee_count": employee_count,
            "numberofemployees": numberofemployees,
            "r__size_of_sales_team": sales_team,
        }

    def test_employee_count_primary(self):
        """employee_count is used when set, even if numberofemployees is also set."""
        from src.hubspot import _compute_employee_count_merged
        assert _compute_employee_count_merged(self._props("300", "500")) == "300"

    def test_numberofemployees_fallback(self):
        """numberofemployees is used when employee_count is None/empty."""
        from src.hubspot import _compute_employee_count_merged
        assert _compute_employee_count_merged(self._props(None, "500")) == "500"

    def test_empty_string_employee_count_falls_back(self):
        """Empty string employee_count triggers fallback to numberofemployees."""
        from src.hubspot import _compute_employee_count_merged
        assert _compute_employee_count_merged(self._props("", "500")) == "500"

    def test_both_empty_returns_empty(self):
        """Both None → empty string."""
        from src.hubspot import _compute_employee_count_merged
        assert _compute_employee_count_merged(self._props(None, None)) == ""

    def test_icp_size_uses_employee_count_over_numberofemployees(self):
        """ICP size is computed from employee_count when set, ignoring numberofemployees."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size("600", "50", "0") == "Enterprise"

    def test_icp_size_falls_back_to_numberofemployees(self):
        """ICP size falls back to numberofemployees when employee_count is blank."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size(None, "300", "0") == "Commercial"

    def test_icp_size_startup_when_both_blank(self):
        """Both blank → 0 employees, no sales team → Startup."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size(None, None, "0") == "Startup"

    def test_icp_size_smb(self):
        """<200 employees and sales_team ≥2 → SMB."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size("50", None, "3") == "SMB"

    def test_icp_size_commercial(self):
        """200–499 employees → Commercial."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size(None, "250", "1") == "Commercial"


# ── Qualified helper (sales_team > 0 OR funding > $5M) ────────────────────────

class TestComputeQualified:
    """_compute_qualified returns 'Yes' if sales_team > 0 OR funding > $5M."""

    def test_yes_when_sales_team_nonzero(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "5", "total_funding": "0"}) == "Yes"

    def test_yes_when_funding_above_5m(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "6000000"}) == "Yes"

    def test_no_when_both_zero(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "0"}) == "No"

    def test_no_when_both_blank(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({}) == "No"

    def test_yes_when_both_met(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "10", "total_funding": "10000000"}) == "Yes"

    def test_no_when_funding_exactly_5m(self):
        from src.hubspot import _compute_qualified
        # Strictly greater than $5M required — exactly 5M is "No"
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "5000000"}) == "No"

    def test_no_when_props_are_none(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": None, "total_funding": None}) == "No"


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
