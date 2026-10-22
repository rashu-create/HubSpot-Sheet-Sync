"""Header guard: never write into the pipeline tab if its header row is not what COLUMN_MAP expects."""

from unittest.mock import MagicMock, patch

import pytest

from src import sheets
from src.header_guard import HeaderMismatchError, check_headers, expected_headers
from src.mapping import COLUMN_MAP

# Row 1 of the live "Sales Pipeline 2026" tab, captured 2026-10-22 (A..AP).
LIVE_HEADERS = [
    "Domain", "Date of First Meeting", "Opportunity?", "Owner?", "Priority", "ICP - Segment",
    "ICP - Size", "Funding", "Employee Count", "Size of Sales Team", "Job Title of Champion",
    "L1 Qualified? [ICP]", "L2 Qualified? [Seniority]", "L3 Qualified? [Intent]",
    "L1 Qualification Comments", "L2 Qualification Comments", "L3 Qualification Comments",
    "Next Steps", "Due Date", "Stage", "Trial Stage", "Trial Start Date", "Trial End Date",
    "Notes from Call", "Source of meeting (First HS)", "Conversion", "Still active?",
    "Trial done?", "Closed Won Reason", "Closed Lost Reason", "Closed Lost Reason",
    "Real Opportunity?", "Source of meeting (Muni Sheet)", "Source of meeting [Refined]",
    "Qualified", "Closure Month", "Opportunity loss reason",
    "Opportunity loss reason - deepdive", "Trial loss reason", " Intent signals", "Deal Value",
    "Domain key",
]

ALL_COLS = [entry[0] for entry in COLUMN_MAP]


def test_every_mapped_column_matches_the_live_header_row():
    # Fails if COLUMN_MAP holds a wrong header name for any column we write.
    assert check_headers(LIVE_HEADERS, ALL_COLS) == []


def test_expected_headers_come_from_column_map():
    exp = expected_headers()
    assert exp["AG"] == "Source of meeting (Muni Sheet)"
    assert exp["AO"] == "Deal Value"
    assert "A" not in exp  # column A is input only


def test_comparison_ignores_case_and_stray_whitespace():
    headers = list(LIVE_HEADERS)
    headers[4] = "  PRIORITY "  # E
    headers[5] = "icp  -   segment"  # F
    assert check_headers(headers, ["E", "F"]) == []


def test_shifted_columns_are_reported():
    # Simulate a column moved into E: every header from E rightwards is off by one.
    moved = LIVE_HEADERS[:4] + ["Source of meeting (Muni Sheet)"] + LIVE_HEADERS[4:32] + LIVE_HEADERS[33:]
    problems = check_headers(moved, ALL_COLS)
    assert problems  # not empty
    assert any(p.startswith("E:") for p in problems)
    assert any(p.startswith("AG:") for p in problems)


def test_a_row_that_is_too_short_is_reported_not_crashed():
    problems = check_headers(LIVE_HEADERS[:10], ["C", "AO"])
    assert len(problems) == 1 and problems[0].startswith("AO:")


def test_only_the_columns_being_written_are_checked():
    broken = list(LIVE_HEADERS)
    broken[18] = "Totally different"  # S
    assert check_headers(broken, ["C", "D"]) == []
    assert check_headers(broken, ["S"]) != []


# ── integration with write_pipeline_rows ─────────────────────────────────────

def _worksheet(header_row):
    ws = MagicMock()
    ws.row_values.return_value = header_row
    spreadsheet = MagicMock()
    spreadsheet.worksheet.return_value = ws
    client = MagicMock()
    client.open_by_key.return_value = spreadsheet
    return client, spreadsheet


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PIPELINE_SHEET_ID", "sheet123")
    monkeypatch.setenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026")


def test_write_is_blocked_when_headers_do_not_match():
    shifted = LIVE_HEADERS[:4] + ["Source of meeting (Muni Sheet)"] + LIVE_HEADERS[4:32] + LIVE_HEADERS[33:]
    client, spreadsheet = _worksheet(shifted)
    with patch.object(sheets, "_get_client", return_value=client):
        with pytest.raises(HeaderMismatchError) as exc:
            sheets.write_pipeline_rows([{"row": 2, "values": {"E": "High", "T": "Trial"}}])
    assert "E:" in str(exc.value)
    spreadsheet.values_batch_update.assert_not_called()


def test_write_goes_ahead_when_headers_match():
    client, spreadsheet = _worksheet(LIVE_HEADERS)
    with patch.object(sheets, "_get_client", return_value=client):
        sheets.write_pipeline_rows([{"row": 2, "values": {"E": "High", "T": "Trial"}}])
    spreadsheet.values_batch_update.assert_called_once()
