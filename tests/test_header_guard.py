"""Header guard: never write into the pipeline tab if its header row is not what COLUMN_MAP expects."""

from unittest.mock import MagicMock, patch

import pytest

from src import sheets
from src.header_guard import HeaderMismatchError, check_headers, expected_headers
from src.mapping import COLUMN_MAP

# Row 1 of the live "Sales Pipeline 2026" tab BEFORE the move (captured 2026-10-22, A..AP).
PRE_MOVE = [
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
# The move: col AG (index 32) goes to E (index 4); old E..AF shift right by one; AH onward stays.
LIVE_HEADERS = PRE_MOVE[:4] + [PRE_MOVE[32]] + PRE_MOVE[4:32] + PRE_MOVE[33:]

ALL_COLS = [entry[0] for entry in COLUMN_MAP]


def test_every_mapped_column_matches_the_live_header_row():
    # Fails if COLUMN_MAP holds a wrong header name for any column we write.
    assert check_headers(LIVE_HEADERS, ALL_COLS) == []


def test_expected_headers_come_from_column_map():
    exp = expected_headers()
    assert exp["E"] == "Source of meeting (Muni Sheet)"
    assert exp["AO"] == "Deal Value"
    assert "AG" not in exp          # AG is now the manual "Real Opportunity?" column
    assert "A" not in exp  # column A is input only


def test_comparison_ignores_case_and_stray_whitespace():
    headers = list(LIVE_HEADERS)
    headers[5] = "  PRIORITY "  # F
    headers[6] = "icp  -   segment"  # G
    assert check_headers(headers, ["F", "G"]) == []


def test_shifted_columns_are_reported():
    # The OLD layout (before the move) must now be rejected: E..AB headers are off by one.
    problems = check_headers(PRE_MOVE, ALL_COLS)
    assert problems
    assert any(p.startswith("E:") for p in problems)
    assert any(p.startswith("U:") for p in problems)


def test_a_row_that_is_too_short_is_reported_not_crashed():
    problems = check_headers(LIVE_HEADERS[:10], ["C", "AO"])
    assert len(problems) == 1 and problems[0].startswith("AO:")


def test_only_the_columns_being_written_are_checked():
    broken = list(LIVE_HEADERS)
    broken[19] = "Totally different"  # T
    assert check_headers(broken, ["C", "D"]) == []
    assert check_headers(broken, ["T"]) != []


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
    client, spreadsheet = _worksheet(PRE_MOVE)
    with patch.object(sheets, "_get_client", return_value=client):
        with pytest.raises(HeaderMismatchError) as exc:
            sheets.write_pipeline_rows([{"row": 2, "values": {"F": "High", "U": "Trial"}}])
    assert "F:" in str(exc.value)
    spreadsheet.values_batch_update.assert_not_called()


def test_write_goes_ahead_when_headers_match():
    client, spreadsheet = _worksheet(LIVE_HEADERS)
    with patch.object(sheets, "_get_client", return_value=client):
        sheets.write_pipeline_rows([{"row": 2, "values": {"F": "High", "U": "Trial"}}])
    spreadsheet.values_batch_update.assert_called_once()
