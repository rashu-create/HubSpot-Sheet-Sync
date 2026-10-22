"""Header guard for the pipeline tab.

Every sync writes cells by column letter. If someone inserts, moves or renames a column the
letters silently point at the wrong data, so before any write we compare row 1 with the headers
in COLUMN_MAP and refuse to write on a mismatch.
"""

import re

from src.mapping import COLUMN_MAP, col_letter_to_index


class HeaderMismatchError(RuntimeError):
    """Row 1 of the pipeline tab does not match COLUMN_MAP."""


def _norm(text: str) -> str:
    """Trim, collapse inner whitespace and ignore case (live headers have stray spaces)."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def expected_headers() -> dict[str, str]:
    """{column letter: expected header} for every column COLUMN_MAP writes."""
    return {entry[0]: entry[1] for entry in COLUMN_MAP}


def check_headers(header_row: list[str], columns: list[str]) -> list[str]:
    """Return one problem string per column in `columns` whose header is not what we expect."""
    expected = expected_headers()
    problems: list[str] = []
    for col in sorted(set(columns), key=col_letter_to_index):
        want = expected.get(col)
        if want is None:
            continue
        idx = col_letter_to_index(col) - 1
        have = header_row[idx] if idx < len(header_row) else ""
        if _norm(have) != _norm(want):
            problems.append(f"{col}: expected {want!r}, found {have!r}")
    return problems
