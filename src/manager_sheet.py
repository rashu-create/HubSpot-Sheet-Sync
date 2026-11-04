"""Reader for the manager's "Sales Pipeline - 2026" sheet (read-only; nothing here ever writes to it).

The manager keeps a monthly tab ("Nov'26 Pipeline") with month blocks side by side. The live block is the
RIGHTMOST one with a "# of Meetings" header in row 2; its company names sit in the "Total" column to the left
of its other headers. Columns are found by header NAME, never by letter, because the manager adds blocks.

Used by:
  - forecast.py                 -> "# of Meetings" column on the forecast tabs
  - scripts/backfill_buyer_designation.py -> "Buyer & Designation" -> HubSpot deal property (one-off)

The parsing functions are pure so they can be tested without Google access.
"""

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MEETINGS_HEADER = "# of Meetings"
BUYER_HEADER = "Buyer & Designation"
NAME_HEADER = "Total"          # the manager's header over the company-name column

_SUFFIX = r"\b(inc|labs?|ai|io|hq|dev|technologies|solutions|qa|corp|co|ltd|llc|com)\b"
_SECTION_ROW = re.compile(r"probability|pipeline|^total$", re.IGNORECASE)    # "High Probability - 80%...", "December Pipeline"
_MONTH_TAB = re.compile(r"^([A-Za-z]{3,9})'(\d{2}) Pipeline$")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


class ManagerSheetError(RuntimeError):
    """The manager's sheet could not be read or its layout is not what we expect."""


@dataclass
class ManagerRow:
    """One company row of the live block."""

    name: str                       # as typed, e.g. "Bryntum | AG Grid"
    buyer: str                      # "Buyer & Designation" cell ("" if the column is missing/blank)
    meetings: int | str | None      # int when numeric, raw text when not (e.g. "4+"), None when blank
    row: int                        # 1-based sheet row


def key(name: str | None) -> str:
    """Normalise a company/deal name for matching (case, punctuation, corporate suffixes)."""
    n = (name or "").lower()
    n = re.sub(r"\.(io|ai|dev|com|sh|so|co)\b", "", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(_SUFFIX, " ", n)
    return re.sub(r"\s+", "", n)


def _norm(cell: str) -> str:
    return " ".join(str(cell).split()).lower()


def pick_tab(titles: list[str], override: str = "") -> str:
    """The tab to read: `override` if given (must exist), else the newest "<Mon>'YY Pipeline" tab."""
    override = (override or "").strip()
    if override:
        if override not in titles:
            raise ManagerSheetError(f"manager tab {override!r} not found")
        return override
    best: tuple[int, int, str] | None = None
    for title in titles:
        m = _MONTH_TAB.match(title.strip())
        if not m or m.group(1)[:3].lower() not in _MONTHS:
            continue
        cand = (int(m.group(2)), _MONTHS.index(m.group(1)[:3].lower()), title)
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None:
        raise ManagerSheetError("no monthly \"<Mon>'YY Pipeline\" tab found in the manager's sheet")
    return best[2]


def _meetings(raw: str) -> int | str | None:
    text = str(raw).strip()
    if not text:
        return None
    return int(text) if re.fullmatch(r"\d+", text) else text


def parse_block(values: list[list[str]]) -> list[ManagerRow]:
    """Parse the live (rightmost "# of Meetings") block of a tab's grid into company rows."""
    header_row = col_meet = None
    for r, row in enumerate(values[:6]):
        hits = [i for i, c in enumerate(row) if _norm(c) == _norm(MEETINGS_HEADER)]
        if hits:
            header_row, col_meet = r, hits[-1]
            break
    if header_row is None:
        raise ManagerSheetError(f"no '{MEETINGS_HEADER}' header found in the first rows of the manager's tab")

    header = values[header_row]
    col_name = next((i for i in range(col_meet - 1, -1, -1) if _norm(header[i]) == _norm(NAME_HEADER)), None)
    if col_name is None:
        raise ManagerSheetError(f"no '{NAME_HEADER}' (company name) header to the left of '{MEETINGS_HEADER}'")
    col_buyer = next((i for i in range(col_name, col_meet + 1) if _norm(header[i]).startswith("buyer")), None)

    def cell(row: list[str], i: int | None) -> str:
        return row[i].strip() if i is not None and i < len(row) else ""

    out: list[ManagerRow] = []
    for r in range(header_row + 1, len(values)):
        row = values[r]
        name = cell(row, col_name)
        if not name or _SECTION_ROW.search(name):
            continue
        out.append(ManagerRow(name=name, buyer=cell(row, col_buyer), meetings=_meetings(cell(row, col_meet)), row=r + 1))
    return out


def _compact(name: str) -> str:
    """Letters and digits only, nothing stripped: "Virtuoso QA" and "VirtuosoQA" both give "virtuosoqa"."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def name_keys(name: str) -> list[str]:
    """Keys a name answers to: normalised + compact form of the whole name and of each part of "A | B".

    The compact form catches spacing differences that key() cannot ("Virtuoso QA" vs "VirtuosoQA"). It is
    stricter than key() (nothing is dropped), so it never makes two different companies collide.
    """
    parts = [name] + (name.split("|") if "|" in name else [])
    keys = [k for p in parts for k in (key(p), _compact(p))]
    return [k for i, k in enumerate(keys) if k and k not in keys[:i]]


def meetings_by_key(rows: list[ManagerRow]) -> dict[str, int | str]:
    """{normalised company name: # of meetings}; blank counts are left out, the first row of a company wins."""
    out: dict[str, int | str] = {}
    for r in rows:
        if r.meetings is None:
            continue
        for k in name_keys(r.name):
            out.setdefault(k, r.meetings)
    return out


def buyer_rows(rows: list[ManagerRow]) -> list[ManagerRow]:
    """Rows that carry a buyer text (blank cells dropped)."""
    return [r for r in rows if r.buyer.strip()]


def load_rows(client=None) -> list[ManagerRow]:
    """Read the live block of the manager's current monthly tab. Raises ManagerSheetError if it cannot."""
    sheet_id = os.getenv("MANAGER_SHEET_ID", "").strip()
    if not sheet_id:
        raise ManagerSheetError("MANAGER_SHEET_ID is not set")
    if client is None:
        import src.sheets as sheets
        client = sheets._get_client()
    try:
        spreadsheet = client.open_by_key(sheet_id)
        tab = pick_tab([w.title for w in spreadsheet.worksheets()], os.getenv("MANAGER_PIPELINE_TAB", ""))
        values = spreadsheet.worksheet(tab).get_all_values()
    except ManagerSheetError:
        raise
    except Exception as exc:                      # network, permission, quota...
        raise ManagerSheetError(f"could not read the manager's sheet: {exc}") from exc
    rows = parse_block(values)
    logger.info("Manager sheet %r: %d company rows", tab, len(rows))
    return rows
