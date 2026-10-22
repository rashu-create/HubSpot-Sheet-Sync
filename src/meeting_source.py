"""Source of meeting for each Sales Pipeline row.

The Meeting Booked vs Done analysis (~/meeting-booked-done-analysis) already decides, for every
booked meeting, its canonical domain and Source of Meeting (aliases, rebookings, vocabulary). It
publishes the result daily to the ANALYSIS sheet, tab "Data". This module reads that tab and
picks, for each pipeline row, the source of the meeting that row belongs to.

Copied from meeting-booked-done-analysis @ 637c588 (keep in sync if the originals change):
  - src_map            <- src/classify.py   src_map
  - norm_domain        <- src/match.py      norm_domain  (pandas-free)
  - validate_alias_rows<- src/alias_tab.py  validate_rows

Resolution order for a pipeline row (domain + "Date of First Meeting" B):
  1. analysis_exact    the meeting whose Meeting Date + Days Late == B (the one the analysis joined)
  2. analysis_before   the latest meeting on or before B + 1 day
  3. analysis_earliest the earliest meeting
  4. legacy            the old "Weekly Meetings Booked update" sheet (sheets.build_sdr_map)
Rebooked child rows ("Rebooking of" filled) and "Unknown" sources are never used.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from src.mapping import normalize_domain

logger = logging.getLogger(__name__)

DATA_TAB = "Data"
ALIAS_TAB = "Domain Aliases"
UNKNOWN = "Unknown"

# Same hardcoded alias the analysis carries in src/match.py.
_HARDCODED_ALIASES = {"usegitai.com": "usegit.ai"}

# ── source vocabulary (copy of classify.src_map) ──────────────────────────────

_SOURCE_MAP = {
    "ads": "Ads",
    "inbound": "Inbound",
    "referral": "Referral",
    "old outbound": "Old outbound",
    "ecosystem": "Ecosystem",
    "kubecon": "KubeCon",
    "akhil": "Akhil",
    "rama": "Rama",
    "ahmed": "Ahmed",
    "dheeraj": "Dheeraj",
    "akash": "Akash",
    "muni": "Muni",
    "harshini": "Harshini",
    "piyush": "Piyush",
    "sanika": "Sanika",
    "suman": "Suman",
}


def src_map(raw) -> str:
    """Normalise source: split on '/' (first token wins), then look up the canonical name."""
    if not raw or str(raw).strip() in ("?", "", "nan"):
        return UNKNOWN
    first = str(raw).strip().split("/")[0].strip().lower()
    return _SOURCE_MAP.get(first, first.title())


# ── domains (copy of match.norm_domain, without pandas) ───────────────────────

_PLACEHOLDERS = {"", "-", "--", "?", "n/a", "na", "tbd", "none", "null", "nil", "nan", "unknown"}


def _is_valid_company(value) -> bool:
    """True if value is a real domain or company name (has a letter/digit, not a placeholder)."""
    if value is None or (isinstance(value, float) and value != value):
        return False
    s = str(value).strip().lower()
    return s not in _PLACEHOLDERS and any(ch.isalnum() for ch in s)


def norm_domain(s, alias_map: dict | None = None) -> str | None:
    """Canonical (primary) domain for a typed domain/company, or None for junk."""
    effective = {**_HARDCODED_ALIASES, **(alias_map or {})}
    if s is None or (isinstance(s, float) and s != s):
        return None
    s = str(s).strip().lower()
    if not _is_valid_company(s):
        return None
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/").split("/")[0]
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)   # "seqera(left)" -> "seqera"
    if not _is_valid_company(s):
        return None
    return effective.get(s, s)


# ── domain aliases (copy of alias_tab.validate_rows) ──────────────────────────

_ALT_COLS = ("Domain 1", "Domain 2", "Domain 3")
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def _alias_norm(d) -> str:
    d = str(d or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.split("/")[0].strip()


def validate_alias_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """-> (usable Active rows with normalised domains, human-readable problems for skipped rows)."""
    active: list[dict] = []
    problems: list[str] = []
    alt_owner: dict[str, str] = {}
    primaries = {
        _alias_norm(r.get("Primary Domain")) for r in rows
        if str(r.get("Status", "")).strip().lower() in ("", "active")
    }
    for i, r in enumerate(rows, start=2):
        status = str(r.get("Status", "")).strip().lower()
        if status not in ("", "active"):
            continue                                       # Pending / Rejected are never used
        primary = _alias_norm(r.get("Primary Domain"))
        alts = [a for a in (_alias_norm(r.get(c)) for c in _ALT_COLS) if a]
        if not primary and not alts:
            continue                                       # blank row
        label = f"row {i} ({r.get('Company Name') or primary or '?'})"
        if not primary or not _DOMAIN.match(primary):
            problems.append(f"{label}: invalid or missing Primary Domain {r.get('Primary Domain')!r}")
            continue
        bad = [a for a in alts if not _DOMAIN.match(a)]
        if bad:
            problems.append(f"{label}: invalid alternate domain(s) {bad}")
            continue
        if primary in alts:
            problems.append(f"{label}: an alternate domain equals the primary ({primary})")
            continue
        chained = [a for a in alts if a in primaries]
        if chained:
            problems.append(f"{label}: {chained} is also a Primary Domain elsewhere (alias chains are not supported)")
            continue
        clash = [a for a in alts if alt_owner.get(a, primary) != primary]
        if clash:
            problems.append(f"{label}: {clash} already mapped to {alt_owner[clash[0]]} — row skipped")
            continue
        for a in alts:
            alt_owner[a] = primary
        active.append({**r, "Primary Domain": primary, **{c: _alias_norm(r.get(c)) for c in _ALT_COLS}})
    return active, problems


def build_alias_map(active: list[dict]) -> dict[str, str]:
    """{secondary domain: primary domain} from validated Active rows."""
    out: dict[str, str] = {}
    for r in active:
        primary = r["Primary Domain"]
        for c in _ALT_COLS:
            alt = r.get(c, "")
            if alt and alt != primary:
                out[alt] = primary
    return out


# ── dates ─────────────────────────────────────────────────────────────────────

_SHEETS_EPOCH = date(1899, 12, 30)


def to_date(value) -> date | None:
    """A Sheets serial number, "6 Jan 2026" or ISO text -> date; None if blank or unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        serial = value
    else:
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        try:
            serial = float(text)
        except ValueError:
            return None
    try:
        d = _SHEETS_EPOCH + timedelta(days=int(serial))
    except (OverflowError, ValueError):
        return None
    return d if 2000 <= d.year <= 2100 else None


# ── the analysis Data tab ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Meeting:
    dom: str
    mtg: date
    src: str
    days_late: int | None
    rebooking_of: str


@dataclass
class AnalysisData:
    meetings: list[Meeting] = field(default_factory=list)
    alias_map: dict[str, str] = field(default_factory=dict)
    alias_problems: list[str] = field(default_factory=list)


_REQUIRED = ("Domain", "Meeting Date", "Source of Meeting", "Days Late", "Rebooking of")


def parse_data_rows(values: list[list], alias_map: dict[str, str]) -> list[Meeting]:
    """Rows of the analysis Data tab (header first) -> meetings. Columns are found by header name."""
    if not values:
        raise ValueError("analysis Data tab is empty")
    header = {str(h).strip().casefold(): i for i, h in enumerate(values[0])}
    missing = [h for h in _REQUIRED if h.casefold() not in header]
    if missing:
        raise ValueError(f"analysis Data tab is missing header(s): {', '.join(missing)}")
    idx = {h: header[h.casefold()] for h in _REQUIRED}

    def cell(row, name):
        i = idx[name]
        return row[i] if i < len(row) else ""

    meetings: list[Meeting] = []
    for row in values[1:]:
        dom = norm_domain(cell(row, "Domain"), alias_map)
        mtg = to_date(cell(row, "Meeting Date"))
        if not dom or mtg is None:
            continue
        try:
            late = int(round(float(cell(row, "Days Late"))))
        except (TypeError, ValueError):
            late = None
        meetings.append(Meeting(
            dom=dom,
            mtg=mtg,
            src=src_map(cell(row, "Source of Meeting")),
            days_late=late,
            rebooking_of=str(cell(row, "Rebooking of") or "").strip(),
        ))
    return meetings


# ── picking the meeting for a pipeline row ────────────────────────────────────

def pick_source(domain: str, first_meeting: date | None, meetings: list[Meeting],
                alias_map: dict[str, str]) -> tuple[str, str] | None:
    """-> (source, rule) for the pipeline row, or None if no usable meeting exists."""
    canon = norm_domain(domain, alias_map)
    if not canon:
        return None
    mine = [m for m in meetings if m.dom == canon and not m.rebooking_of and m.src != UNKNOWN]
    if not mine:
        return None
    if first_meeting is not None:
        exact = [m for m in mine
                 if m.days_late is not None and m.mtg + timedelta(days=m.days_late) == first_meeting]
        if exact:
            return min(exact, key=lambda m: m.mtg).src, "analysis_exact"
        before = [m for m in mine if m.mtg <= first_meeting + timedelta(days=1)]
        if before:
            return max(before, key=lambda m: m.mtg).src, "analysis_before"
    return min(mine, key=lambda m: m.mtg).src, "analysis_earliest"


def resolve_sources(rows: list[tuple[int, str, date | None]], analysis: AnalysisData,
                    legacy: dict[str, str]) -> dict[int, tuple[str, str]]:
    """{sheet row: (source, rule)} for every row that has a source; unresolved rows are omitted."""
    out: dict[int, tuple[str, str]] = {}
    for row, domain, first_meeting in rows:
        picked = pick_source(domain, first_meeting, analysis.meetings, analysis.alias_map)
        if picked is None:
            canon = norm_domain(domain, analysis.alias_map)
            raw = legacy.get(canon) if canon else None
            if not raw:
                raw = legacy.get(normalize_domain(domain))
            legacy_src = src_map(raw)
            if legacy_src != UNKNOWN:
                picked = (legacy_src, "legacy")
        if picked is not None:
            out[row] = picked
    return out


# ── loading from Google Sheets ────────────────────────────────────────────────

_ALIAS_HEADERS = [
    "Company Name", "Primary Domain", "Domain 1", "Domain 2", "Domain 3", "Total Domains",
    "Relationship", "Redirects", "M&A / Notes", "Source", "Status",
]


def load_analysis() -> AnalysisData:
    """Read the analysis sheet's Domain Aliases and Data tabs (ANALYSIS_SHEET_ID).

    Raises if the sheet or the Data tab cannot be read or changed shape, so the caller can skip
    the column for that run instead of writing guesses. An unreadable alias tab only degrades.
    """
    from src import sheets   # imported here: sheets imports this module

    sheet_id = os.getenv("ANALYSIS_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("ANALYSIS_SHEET_ID is not set")

    spreadsheet = sheets._get_client().open_by_key(sheet_id)

    alias_map: dict[str, str] = {}
    problems: list[str] = []
    try:
        raw = spreadsheet.worksheet(ALIAS_TAB).get("A1:K3000", value_render_option="FORMATTED_VALUE")
        rows = [
            dict(zip(_ALIAS_HEADERS, list(r) + [""] * (len(_ALIAS_HEADERS) - len(r))))
            for r in raw[1:]
        ]
        active, problems = validate_alias_rows(rows)
        alias_map = build_alias_map(active)
    except Exception as exc:
        logger.warning("Could not read the %r tab: %s", ALIAS_TAB, exc)
        problems = [f"{ALIAS_TAB} tab unreadable: {exc}"]

    values = spreadsheet.worksheet(DATA_TAB).get("A1:AZ6000", value_render_option="UNFORMATTED_VALUE")
    meetings = parse_data_rows(values, alias_map)
    logger.info("Analysis data: %d meetings, %d aliases, %d alias problems",
                len(meetings), len(alias_map), len(problems))
    return AnalysisData(meetings=meetings, alias_map=alias_map, alias_problems=problems)


def change_kind(current: str, new: str | None) -> str:
    """How a resolved source compares with what the sheet holds now (for the review CSV)."""
    has_current = bool(str(current or "").strip())
    if new is None:
        return "UNRESOLVED_KEEPS_CURRENT" if has_current else "UNRESOLVED_BLANK"
    if not has_current:
        return "FILLED"
    return "SAME" if src_map(current) == new else "CHANGED"
