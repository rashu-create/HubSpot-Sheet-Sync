"""The one-shot Source-of-meeting move (AG -> E): request building, layout checks and cell-by-cell verification."""
import pytest

from src.column_move import (
    DEST_E, SRC_AG, apply_move_to_rows, is_post_move, is_pre_move, move_request, rollback_request, verify_move,
)
from tests.test_header_guard import LIVE_HEADERS, PRE_MOVE


def _rows(n=3):
    """Fake pipeline rows in the PRE-move layout: header + n data rows, every cell unique."""
    out = [list(PRE_MOVE)]
    for r in range(n):
        out.append([f"r{r}c{c}" for c in range(len(PRE_MOVE))])
    return out


def test_forward_request_moves_column_ag_before_column_e():
    req = move_request(sheet_id=77)
    assert req == {"moveDimension": {
        "source": {"sheetId": 77, "dimension": "COLUMNS", "startIndex": 32, "endIndex": 33},
        "destinationIndex": 4,
    }}
    assert (SRC_AG, DEST_E) == (32, 4)


def test_rollback_request_moves_column_e_back_after_column_ag():
    # destinationIndex is expressed in the sheet BEFORE the move, so "after AG" is 33.
    assert rollback_request(77) == {"moveDimension": {
        "source": {"sheetId": 77, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5},
        "destinationIndex": 33,
    }}


def test_layout_detection_on_the_real_header_rows():
    assert is_pre_move(PRE_MOVE) and not is_post_move(PRE_MOVE)
    assert is_post_move(LIVE_HEADERS) and not is_pre_move(LIVE_HEADERS)


def test_layout_detection_rejects_anything_else():
    garbage = ["x"] * 42
    assert not is_pre_move(garbage) and not is_post_move(garbage)
    assert not is_pre_move([]) and not is_post_move([])


def test_apply_move_puts_ag_in_e_and_shifts_e_to_af_right_by_one():
    old = _rows(1)[1]
    new = apply_move_to_rows([old])[0]
    assert new[4] == old[32]                     # E <- AG
    assert new[5:33] == old[4:32]                # F..AG <- E..AF
    assert new[:4] == old[:4] and new[33:] == old[33:]   # A-D and AH.. unchanged
    assert len(new) == len(old)


def test_apply_move_to_the_header_row_gives_the_post_move_headers():
    assert apply_move_to_rows([PRE_MOVE])[0] == LIVE_HEADERS


def test_verify_passes_when_the_new_sheet_is_exactly_the_moved_backup():
    old = _rows()
    assert verify_move(old, apply_move_to_rows(old)) == []


def test_verify_ignores_trailing_blank_cells_that_sheets_trims():
    old = _rows()
    old[2] = old[2][:10] + [""] * (len(old[2]) - 10)          # a row whose tail is empty
    new = apply_move_to_rows(old)
    while new[2] and new[2][-1] == "":
        new[2].pop()                                           # ...and the live read trimmed it
    assert verify_move(old, new) == []


def test_verify_reports_missing_data_even_when_the_row_is_short():
    old = _rows()
    new = apply_move_to_rows(old)
    new[2] = new[2][:30]
    assert verify_move(old, new)


def test_verify_reports_the_cell_that_differs():
    old = _rows()
    new = apply_move_to_rows(old)
    new[2][10] = "TAMPERED"
    problems = verify_move(old, new)
    assert len(problems) == 1 and "row 3" in problems[0] and "K" in problems[0]


def test_verify_reports_a_row_count_change():
    old = _rows()
    assert any("row count" in p for p in verify_move(old, apply_move_to_rows(old)[:-1]))
