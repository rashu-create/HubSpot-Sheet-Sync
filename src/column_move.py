"""One-shot move of "Source of meeting (Muni Sheet)" from col AG to col E (2026-10).

Pure helpers used by scripts/move_source_column.py: the Sheets API request, layout detection from row 1,
and a cell-by-cell check that the live tab equals the backup with the move applied. The move itself is ONE
`moveDimension` call, so values, validation, formatting and formulas in the same spreadsheet travel with the column.
"""
import re

from src.header_guard import _norm
from src.mapping import col_index_to_letter

SRC_AG = 32   # 0-based index of col AG
DEST_E = 4    # 0-based index of col E: "insert before the column currently at E"
_WIDTH = 42   # A..AP

_PRE_E, _PRE_AG = "Priority", "Source of meeting (Muni Sheet)"


def move_request(sheet_id: int) -> dict:
    """moveDimension: AG goes before the column that is currently E."""
    return {"moveDimension": {
        "source": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": SRC_AG, "endIndex": SRC_AG + 1},
        "destinationIndex": DEST_E,
    }}


def rollback_request(sheet_id: int) -> dict:
    """Reverse: E goes back after AG. destinationIndex counts in the sheet BEFORE the move, hence AG + 1."""
    return {"moveDimension": {
        "source": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": DEST_E, "endIndex": DEST_E + 1},
        "destinationIndex": SRC_AG + 1,
    }}


def _cell(header_row: list, i: int) -> str:
    return _norm(header_row[i]) if i < len(header_row) else ""


def is_pre_move(header_row: list) -> bool:
    """Row 1 still has Priority in E and the Muni-sheet source in AG."""
    return _cell(header_row, DEST_E) == _norm(_PRE_E) and _cell(header_row, SRC_AG) == _norm(_PRE_AG)


def is_post_move(header_row: list) -> bool:
    """Row 1 has the Muni-sheet source in E and Priority in F."""
    return _cell(header_row, DEST_E) == _norm(_PRE_AG) and _cell(header_row, DEST_E + 1) == _norm(_PRE_E)


def _pad(row: list, width: int) -> list:
    return list(row) + [""] * (width - len(row))


def apply_move_to_rows(rows: list[list]) -> list[list]:
    """What the sheet must look like after the move: AG -> E, old E..AF -> F..AG, everything else unchanged."""
    width = max([_WIDTH] + [len(r) for r in rows])
    out = []
    for r in rows:
        r = _pad(r, width)
        out.append(r[:DEST_E] + [r[SRC_AG]] + r[DEST_E:SRC_AG] + r[SRC_AG + 1:])
    return out


def diff_rows(expected: list[list], got: list[list], limit: int = 50) -> list[str]:
    """Cell-by-cell differences between two grids (trailing blanks ignored); empty list = identical."""
    if len(expected) != len(got):
        return [f"row count differs: expected {len(expected)} vs live {len(got)}"]
    width = max(len(r) for r in expected + got)
    problems: list[str] = []
    for r, (exp, have) in enumerate(zip(expected, got), start=1):
        exp, have = _pad(exp, width), _pad(have, width)
        for c in range(width):
            if exp[c] != have[c]:
                problems.append(f"row {r} col {col_index_to_letter(c + 1)}: expected {exp[c]!r}, found {have[c]!r}")
                if len(problems) >= limit:
                    problems.append("... (stopped at the first %d)" % limit)
                    return problems
    return problems


def verify_move(old_rows: list[list], new_rows: list[list], limit: int = 50) -> list[str]:
    """Problems (empty = identical) between the backup with the move applied and the live tab, cell by cell."""
    if len(old_rows) != len(new_rows):
        return [f"row count differs: backup {len(old_rows)} vs live {len(new_rows)}"]
    return diff_rows(apply_move_to_rows(old_rows), new_rows, limit)
