"""Column map, formatters, and property lists for HubSpot→Sheet sync.

This module defines EXACTLY what properties to fetch, what columns to write,
and how to transform each value. No logic here depends on external services.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── HubSpot property lists ────────────────────────────────────────────────────

DEAL_PROPERTIES = [
    "hubspot_owner_id",
    "dealstage",
    "pipeline",
    "opportunity",
    "hs_priority",
    "l1_qualified_icp",
    "l2_qualified_seniority",
    "l3_qualified_intent",
    "l1_qualification_comments",
    "l2_qualification_comments",
    "l3_qualification_comments",
    "next_steps_management",
    "next_steps_due_date_management",
    "trial_start_date",
    "trial_end_date",
    "conversion",
    "trial_done",
    "closedate",
    "closed_lost_reasons",
    "closed_lost_details",
    "createdate",
]

COMPANY_PROPERTIES = [
    "domain",
    "name",
    "hubspot_owner_id",
    "icp",
    "icp_size",
    "total_funding",
    "numberofemployees",
    "r__size_of_sales_team",
    "notes_from_call",
    "r_l2_qualification_comments_form",
]

# ── Column map ────────────────────────────────────────────────────────────────
# (col_letter, sheet_header, source, property_key, formatter)
# source: "deal" | "company" | "sdr"
# col A (domain) is INPUT — NEVER written.

COLUMN_MAP = [
    # col B = Date of First Meeting — SKIP (manual)
    ("C",  "Opportunity?",                      "deal",    "opportunity",                       "passthrough"),
    ("D",  "Owner?",                            "deal",    "hubspot_owner_id",                  "owner_name"),
    ("E",  "Priority",                          "deal",    "hs_priority",                       "capitalize"),
    ("F",  "ICP - Segment",                     "company", "icp",                               "passthrough"),
    ("G",  "ICP - Size",                        "company", "icp_size",                          "passthrough"),
    ("H",  "Funding",                           "company", "total_funding",                     "number"),
    ("I",  "Employee Count",                    "company", "numberofemployees",                 "number"),
    ("J",  "Size of Sales Team",                "company", "r__size_of_sales_team",             "number"),
    ("K",  "Job Title of Champion",             "company", "r_l2_qualification_comments_form",  "passthrough"),
    ("L",  "L1 Qualified? [ICP]",               "deal",    "l1_qualified_icp",                  "passthrough"),
    ("M",  "L2 Qualified? [Seniority]",         "deal",    "l2_qualified_seniority",            "passthrough"),
    ("N",  "L3 Qualified? [Intent]",            "deal",    "l3_qualified_intent",               "passthrough"),
    ("O",  "L1 Qualification Comments",         "deal",    "l1_qualification_comments",         "passthrough"),
    ("P",  "L2 Qualification Comments",         "deal",    "l2_qualification_comments",         "passthrough"),
    ("Q",  "L3 Qualification Comments",         "deal",    "l3_qualification_comments",         "passthrough"),
    ("R",  "Next Steps",                        "deal",    "next_steps_management",             "passthrough"),
    ("S",  "Due Date",                          "deal",    "next_steps_due_date_management",    "date_dmy"),
    ("T",  "Stage",                             "deal",    "dealstage",                         "stage_label"),
    ("U",  "Trial Start Date",                  "deal",    "trial_start_date",                  "date_dmy"),
    ("V",  "Trial End Date",                    "deal",    "trial_end_date",                    "date_dmy"),
    ("W",  "Notes from Call",                   "company", "notes_from_call",                   "passthrough"),
    # col X = SKIP
    ("Y",  "Conversion",                        "deal",    "conversion",                        "passthrough"),
    # col Z = SKIP (Still active? — manual)
    ("AA", "Trial done?",                       "deal",    "trial_done",                        "passthrough"),
    # cols AB, AC, AD = SKIP
    # col AE = SKIP (Real Opportunity? — manual)
    ("AF", "Source of meeting",                 "sdr",     "sdr_lookup",                        "passthrough"),
    # col AG = SKIP (manual)
    ("AH", "Closure Month",                     "deal",    "closedate",                         "month_year"),
    ("AI", "Opportunity loss reason",           "deal",    "closed_lost_reasons",               "passthrough"),
    ("AJ", "Opportunity loss reason - deepdive","deal",    "closed_lost_details",               "passthrough"),
    # col AK = SKIP (Trial loss reason — manual)
]

# ── Column letter ↔ index helpers ─────────────────────────────────────────────

def col_letter_to_index(col: str) -> int:
    """Convert column letter(s) to 1-based column index. A→1, Z→26, AA→27, AF→32."""
    col = col.upper().strip()
    result = 0
    for ch in col:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


def col_index_to_letter(idx: int) -> str:
    """Convert 1-based column index to column letter(s). 1→A, 26→Z, 27→AA."""
    result = ""
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


# ── Formatters ────────────────────────────────────────────────────────────────

_MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp (with or without ms/tz) into a datetime."""
    if not value:
        return None
    # Remove trailing Z / +00:00 for uniform parsing; handle milliseconds
    v = value.strip()
    # Try common HubSpot formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    logger.debug("Cannot parse date value: %r", value)
    return None


def fmt_passthrough(value: str | None) -> str:
    """Return value as-is, stripped. None → empty string."""
    if value is None:
        return ""
    return str(value).strip()


def fmt_capitalize(value: str | None) -> str:
    """Capitalise first letter only: 'low' → 'Low'."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    return v[0].upper() + v[1:]


def fmt_number(value: str | None) -> str:
    """Return numeric string or '' if None/empty/zero."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    # Keep as string (no currency formatting) — sheet handles display
    try:
        float(v)  # validate it's actually a number
        return v
    except ValueError:
        return v


def fmt_date_dmy(value: str | None) -> str:
    """'2026-07-17T...' → '17 Jul 2026'."""
    dt = _parse_iso(value)
    if dt is None:
        return ""
    return f"{dt.day} {_MONTH_ABBR[dt.month]} {dt.year}"


def fmt_month_year(value: str | None) -> str:
    """'2026-08-01T...' → 'Aug 2026'."""
    dt = _parse_iso(value)
    if dt is None:
        return ""
    return f"{_MONTH_ABBR[dt.month]} {dt.year}"


# Formatter registry — resolved at call time so stage_label can inject the cache
_FORMATTERS = {
    "passthrough": fmt_passthrough,
    "capitalize":  fmt_capitalize,
    "number":      fmt_number,
    "date_dmy":    fmt_date_dmy,
    "month_year":  fmt_month_year,
}


def apply_formatter(name: str, value: str | None, stage_label_cache: dict | None = None) -> str:
    """Apply a named formatter to value.

    'owner_name' and 'stage_label' are handled upstream (need API/cache data);
    this function handles the pure-transform formatters only. Callers that need
    owner_name / stage_label should resolve those values before calling apply_formatter
    with 'passthrough' instead.
    """
    fn = _FORMATTERS.get(name)
    if fn is None:
        logger.warning("Unknown formatter %r — using passthrough", name)
        fn = fmt_passthrough
    return fn(value)


# ── Domain normalisation (shared with sheets.py SDR map) ─────────────────────

_PROTOCOL_RE = re.compile(r"^https?://", re.IGNORECASE)


def normalize_domain(domain: str) -> str:
    """Lowercase, strip whitespace, strip protocol and www prefix."""
    d = domain.strip().lower()
    d = _PROTOCOL_RE.sub("", d)
    if d.startswith("www."):
        d = d[4:]
    # Strip trailing slash
    d = d.rstrip("/")
    return d
