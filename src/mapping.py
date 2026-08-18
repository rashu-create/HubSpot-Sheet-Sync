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
    "hs_all_owner_ids",
    "dealstage",
    "pipeline",
    "opportunity",
    "hs_priority",
    "next_steps_management",
    "next_steps_due_date_management",
    "trial_status",
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
    "total_funding",
    "numberofemployees",
    "r__size_of_sales_team",
    "l1_qualified__",
    "l2_qualified__",
    "l3_qualified____cloned_",
    "r_l1_qualification_comments_form",
    "r_l2_qualification_comments_form",
    "r_l3_qualification_comments_form",
    "next_steps",
    "next_steps_due_date",
    "notes_from_call",
]

# ── Column map ────────────────────────────────────────────────────────────────
# (col_letter, sheet_header, source, property_key, formatter)
# source: "deal" | "company" | "sdr"
# col A (domain) is INPUT — NEVER written.

COLUMN_MAP = [
    # col B = Date of First Meeting — SKIP (manual)
    ("C",  "Opportunity?",                       "deal",     "opportunity",                       "passthrough"),
    ("D",  "Owner?",                             "computed", "owner_first_names",                 "passthrough"),
    ("E",  "Priority",                           "deal",     "hs_priority",                       "capitalize"),
    ("F",  "ICP - Segment",                      "company",  "icp",                               "icp_segment"),
    ("G",  "ICP - Size",                         "computed", "icp_size",                          "passthrough"),
    ("H",  "Funding",                            "company",  "total_funding",                     "number"),
    ("I",  "Employee Count",                     "company",  "numberofemployees",                 "number"),
    ("J",  "Size of Sales Team",                 "company",  "r__size_of_sales_team",             "number"),
    ("K",  "Job Title of Champion",              "company",  "r_l2_qualification_comments_form",  "passthrough"),
    ("L",  "L1 Qualified? [ICP]",                "company",  "l1_qualified__",                    "l1_qualified"),
    ("M",  "L2 Qualified? [Seniority]",          "company",  "l2_qualified__",                    "l2_qualified"),
    ("N",  "L3 Qualified? [Intent]",             "company",  "l3_qualified____cloned_",           "l3_qualified"),
    ("O",  "L1 Qualification Comments",          "company",  "r_l1_qualification_comments_form",  "passthrough"),
    ("P",  "L2 Qualification Comments",          "company",  "r_l2_qualification_comments_form",  "passthrough"),
    ("Q",  "L3 Qualification Comments",          "company",  "r_l3_qualification_comments_form",  "passthrough"),
    ("R",  "Next Steps",                         "computed", "next_steps_r",                      "passthrough"),
    ("S",  "Due Date",                           "computed", "next_steps_s",                      "date_dmy"),
    ("T",  "Stage",                              "deal",     "dealstage",                         "stage_label"),
    ("U",  "Trial Stage",                        "deal",     "trial_status",                      "passthrough"),
    ("V",  "Trial Start Date",                   "deal",     "trial_start_date",                  "date_dmy"),
    ("W",  "Trial End Date",                     "deal",     "trial_end_date",                    "date_dmy"),
    ("X",  "Notes from Call",                    "company",  "notes_from_call",                   "passthrough"),
    # col Y = SKIP
    ("Z",  "Conversion",                         "deal",     "conversion",                        "passthrough"),
    ("AA", "Still active?",                      "computed", "still_active",                      "passthrough"),
    ("AB", "Trial done?",                        "deal",     "trial_done",                        "trial_done"),
    # cols AC, AD, AE = SKIP
    # col AF = SKIP (Real Opportunity? — manual)
    ("AG", "Source of meeting",                  "sdr",      "sdr_lookup",                        "passthrough"),
    # col AH = SKIP (manual)
    ("AI", "Closure Month",                      "deal",     "closedate",                         "month_year"),
    ("AJ", "Opportunity loss reason",            "deal",     "closed_lost_reasons",               "passthrough"),
    ("AK", "Opportunity loss reason - deepdive", "deal",     "closed_lost_details",               "passthrough"),
    # col AL = SKIP (Trial loss reason — manual)
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


_ICP_SEGMENT_MAP: dict[str, str] = {
    "open source":            "OSS",
    "open source affiliated": "OSS Affiliated",
    "non open source":        "Closed Source",
    "agency / others":        "Agency/Other",
    "agency/others":          "Agency/Other",
    "agency / other":         "Agency/Other",
}


def fmt_icp_segment(value: str | None) -> str:
    """Normalise HubSpot icp company value to sheet dropdown label."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    mapped = _ICP_SEGMENT_MAP.get(v.lower().strip())
    if mapped is None:
        logger.warning("Unknown ICP segment value: %r — writing raw", v)
        return v
    return mapped


_TRIAL_DONE_MAP: dict[str, str] = {
    "to-start":   "To start",
    "to_start":   "To start",
    "to start":   "To start",
    "yes":        "Yes",
    "no":         "No",
}


def fmt_trial_done(value: str | None) -> str:
    """Normalise trial_done dropdown: 'To-Start' → 'To start', etc."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    mapped = _TRIAL_DONE_MAP.get(v.lower().strip())
    if mapped is not None:
        return mapped
    return v


_L1_MAP: dict[str, str] = {
    "yes":      "Yes",
    "maybe":    "Maybe",
    "weak fit": "No",
    "no":       "No",
}

_L2_MAP: dict[str, str] = {
    "yes":    "Yes",
    "may be": "Maybe",
    "maybe":  "Maybe",
    "no":     "No",
}

_L3_MAP: dict[str, str] = {
    "yes":   "High",
    "maybe": "Medium",
    "no":    "Low",
}


def fmt_l1_qualified(value: str | None) -> str:
    """L1 Qualified: Yes→Yes, Maybe→Maybe, Weak fit/No→No."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    return _L1_MAP.get(v.lower().strip(), v)


def fmt_l2_qualified(value: str | None) -> str:
    """L2 Qualified: Yes→Yes, May be/Maybe→Maybe, No→No."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    return _L2_MAP.get(v.lower().strip(), v)


def fmt_l3_qualified(value: str | None) -> str:
    """L3 Qualified (Intent): Yes→High, Maybe→Medium, No→Low."""
    v = fmt_passthrough(value)
    if not v:
        return ""
    return _L3_MAP.get(v.lower().strip(), v)


# Formatter registry — resolved at call time so stage_label can inject the cache
_FORMATTERS = {
    "passthrough":    fmt_passthrough,
    "capitalize":     fmt_capitalize,
    "number":         fmt_number,
    "date_dmy":       fmt_date_dmy,
    "month_year":     fmt_month_year,
    "icp_segment":    fmt_icp_segment,
    "trial_done":     fmt_trial_done,
    "l1_qualified":   fmt_l1_qualified,
    "l2_qualified":   fmt_l2_qualified,
    "l3_qualified":   fmt_l3_qualified,
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
