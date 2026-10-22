"""Source of meeting: pure logic copied from / consistent with meeting-booked-done-analysis."""

from datetime import date

import pytest

from src.meeting_source import (
    AnalysisData,
    Meeting,
    build_alias_map,
    norm_domain,
    parse_data_rows,
    pick_source,
    resolve_sources,
    src_map,
    to_date,
    validate_alias_rows,
)


# ── src_map (parity with meeting-booked-done-analysis src/classify.py) ───────

@pytest.mark.parametrize("raw,expected", [
    ("ads", "Ads"),
    ("Ads", "Ads"),
    ("Old Outbound", "Old outbound"),
    ("Akhil / Rama", "Akhil"),          # first token wins
    ("  muni ", "Muni"),
    ("?", "Unknown"),
    ("", "Unknown"),
    (None, "Unknown"),
    ("nan", "Unknown"),
    ("Founder's Network", "Founder'S Network"),   # unknown values are title-cased (same as the analysis)
    ("ae reactivated", "Ae Reactivated"),
])
def test_src_map(raw, expected):
    assert src_map(raw) == expected


# ── norm_domain ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("https://www.Example.com/path", "example.com"),
    ("  Traefik.io ", "traefik.io"),
    ("seqera(left)", "seqera"),
    ("n/a", None),
    ("--", None),
    ("", None),
    (None, None),
    (float("nan"), None),
])
def test_norm_domain(raw, expected):
    assert norm_domain(raw, {}) == expected


def test_norm_domain_applies_alias_map():
    assert norm_domain("splunk.com", {"splunk.com": "cisco.com"}) == "cisco.com"


def test_norm_domain_has_the_usegit_hardcoded_alias():
    assert norm_domain("usegitai.com", {}) == "usegit.ai"


# ── alias validation (parity with alias_tab.validate_rows) ───────────────────

def _alias(primary, *alts, status="Active", name="Co"):
    cols = dict(zip(("Domain 1", "Domain 2", "Domain 3"), list(alts) + ["", "", ""]))
    return {"Company Name": name, "Primary Domain": primary, "Status": status, **cols}


def test_active_and_blank_status_rows_are_used_pending_and_rejected_are_not():
    rows = [
        _alias("cisco.com", "splunk.com"),
        _alias("echo.ai", "minimus.io", status=""),
        _alias("a.com", "b.com", status="Pending"),
        _alias("c.com", "d.com", status="Rejected"),
    ]
    active, problems = validate_alias_rows(rows)
    assert build_alias_map(active) == {"splunk.com": "cisco.com", "minimus.io": "echo.ai"}
    assert problems == []


def test_chained_alias_rows_are_skipped_and_reported():
    rows = [_alias("cisco.com", "splunk.com"), _alias("splunk.com", "other.io")]
    active, problems = validate_alias_rows(rows)
    assert build_alias_map(active) == {"other.io": "splunk.com"}
    assert any("chain" in p for p in problems)


def test_clashing_alternate_is_skipped_and_reported():
    rows = [_alias("a.com", "x.io"), _alias("b.com", "x.io")]
    active, problems = validate_alias_rows(rows)
    assert build_alias_map(active) == {"x.io": "a.com"}
    assert any("already mapped" in p for p in problems)


def test_invalid_domains_are_reported():
    active, problems = validate_alias_rows([_alias("not a domain", "x.io")])
    assert active == [] and problems


# ── date parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (46028, date(2026, 1, 6)),            # Sheets serial (UNFORMATTED_VALUE)
    (46028.0, date(2026, 1, 6)),
    ("6 Jan 2026", date(2026, 1, 6)),
    ("2026-01-06", date(2026, 1, 6)),
    ("", None),
    (None, None),
    ("TBD", None),
    ("0", None),
])
def test_to_date(value, expected):
    assert to_date(value) == expected


# ── Data tab parsing (by header name, never by position) ─────────────────────

def test_parse_data_rows_reads_columns_by_header_name():
    values = [
        ["Rebooking of", "Days Late", "Source of Meeting", "Meeting Date", "Domain", "Other"],
        ["", 3, "Ads", 46028, "acme.com", "x"],
        ["acme.com|46028", "", "Inbound", 46040, "Acme.com", "x"],
        ["", "", "Referral", "", "nodate.com", ""],       # no meeting date -> skipped
        ["", "", "Referral", 46030, "", ""],              # no domain -> skipped
    ]
    got = parse_data_rows(values, {})
    assert got == [
        Meeting(dom="acme.com", mtg=date(2026, 1, 6), src="Ads", days_late=3, rebooking_of=""),
        Meeting(dom="acme.com", mtg=date(2026, 1, 18), src="Inbound", days_late=None,
                rebooking_of="acme.com|46028"),
    ]


def test_parse_data_rows_missing_header_raises():
    with pytest.raises(ValueError, match="Days Late"):
        parse_data_rows([["Domain", "Meeting Date", "Source of Meeting", "Rebooking of"]], {})


def test_parse_data_rows_applies_aliases_to_the_domain():
    values = [
        ["Domain", "Meeting Date", "Source of Meeting", "Days Late", "Rebooking of"],
        ["splunk.com", 46028, "Ads", 0, ""],
    ]
    assert parse_data_rows(values, {"splunk.com": "cisco.com"})[0].dom == "cisco.com"


# ── pick_source ──────────────────────────────────────────────────────────────

def M(dom, mtg, src, days_late=None, rebooking_of=""):
    return Meeting(dom=dom, mtg=mtg, src=src, days_late=days_late, rebooking_of=rebooking_of)


D = date  # shorthand


def test_exact_join_wins_even_over_a_later_meeting():
    # vercel.com-style: three meetings, three sources. The pipeline row's first meeting is Mar 10,
    # which is the meeting the analysis joined (Mar 4 + 6 days late).
    meetings = [
        M("v.com", D(2026, 1, 5), "Akhil", days_late=None),
        M("v.com", D(2026, 3, 4), "Ads", days_late=6),
        M("v.com", D(2026, 5, 1), "Old outbound", days_late=None),
    ]
    assert pick_source("v.com", D(2026, 3, 10), meetings, {}) == ("Ads", "analysis_exact")


def test_exact_tie_prefers_the_earliest_meeting():
    meetings = [M("t.com", D(2026, 2, 10), "B", days_late=0), M("t.com", D(2026, 2, 8), "A", days_late=2)]
    assert pick_source("t.com", D(2026, 2, 10), meetings, {}) == ("A", "analysis_exact")


def test_without_an_exact_row_uses_the_latest_meeting_on_or_before_the_date():
    meetings = [M("t.com", D(2026, 2, 1), "A"), M("t.com", D(2026, 2, 8), "B"), M("t.com", D(2026, 3, 1), "C")]
    assert pick_source("t.com", D(2026, 2, 10), meetings, {}) == ("B", "analysis_before")


def test_one_day_slack_for_the_12pm_ist_cutoff():
    meetings = [M("t.com", D(2026, 2, 11), "A")]
    assert pick_source("t.com", D(2026, 2, 10), meetings, {}) == ("A", "analysis_before")


def test_falls_back_to_the_earliest_meeting_when_all_are_later():
    meetings = [M("t.com", D(2026, 4, 1), "B"), M("t.com", D(2026, 3, 1), "A")]
    assert pick_source("t.com", D(2026, 2, 10), meetings, {}) == ("A", "analysis_earliest")


def test_missing_first_meeting_date_uses_the_earliest_meeting():
    meetings = [M("t.com", D(2026, 4, 1), "B"), M("t.com", D(2026, 3, 1), "A")]
    assert pick_source("t.com", None, meetings, {}) == ("A", "analysis_earliest")


def test_rebooked_child_rows_are_ignored():
    meetings = [
        M("t.com", D(2026, 2, 1), "Ads"),
        M("t.com", D(2026, 2, 9), "Referral", days_late=1, rebooking_of="t.com|46054"),
    ]
    assert pick_source("t.com", D(2026, 2, 10), meetings, {}) == ("Ads", "analysis_before")


def test_unknown_sources_are_ignored():
    meetings = [M("t.com", D(2026, 2, 1), "Unknown", days_late=0), M("t.com", D(2026, 1, 1), "Ads")]
    assert pick_source("t.com", D(2026, 2, 1), meetings, {}) == ("Ads", "analysis_before")


def test_unknown_only_means_unresolved():
    assert pick_source("t.com", D(2026, 2, 1), [M("t.com", D(2026, 2, 1), "Unknown")], {}) is None


def test_no_meetings_for_the_domain_is_unresolved():
    assert pick_source("t.com", D(2026, 2, 1), [M("other.com", D(2026, 2, 1), "Ads")], {}) is None


def test_pipeline_domain_is_canonicalised_through_aliases():
    meetings = [M("cisco.com", D(2026, 2, 1), "Ads", days_late=0)]
    assert pick_source("Splunk.com", D(2026, 2, 1), meetings, {"splunk.com": "cisco.com"}) == (
        "Ads", "analysis_exact")


# ── resolve_sources ──────────────────────────────────────────────────────────

def test_resolve_prefers_analysis_then_legacy_and_omits_unresolved():
    analysis = AnalysisData(
        meetings=[M("a.com", D(2026, 2, 1), "Ads", days_late=0)], alias_map={}, alias_problems=[])
    rows = [
        (2, "a.com", D(2026, 2, 1)),     # analysis
        (3, "b.com", D(2026, 2, 1)),     # legacy only
        (4, "c.com", D(2026, 2, 1)),     # nowhere
    ]
    legacy = {"a.com": "Akhil", "b.com": "ads", "c.com": "?"}
    got = resolve_sources(rows, analysis, legacy)
    assert got == {2: ("Ads", "analysis_exact"), 3: ("Ads", "legacy")}


def test_legacy_blank_or_unknown_is_not_a_resolution():
    analysis = AnalysisData(meetings=[], alias_map={}, alias_problems=[])
    assert resolve_sources([(2, "x.com", None)], analysis, {"x.com": ""}) == {}


def test_legacy_lookup_uses_the_alias_aware_domain():
    analysis = AnalysisData(meetings=[], alias_map={"splunk.com": "cisco.com"}, alias_problems=[])
    got = resolve_sources([(2, "splunk.com", None)], analysis, {"cisco.com": "Ads"})
    assert got == {2: ("Ads", "legacy")}


# ── change_kind (used by scripts/source_diff.py) ─────────────────────────────

@pytest.mark.parametrize("current,new,kind", [
    ("", "Ads", "FILLED"),
    ("Ads", "Ads", "SAME"),
    ("ads", "Ads", "SAME"),                 # the sheet may hold older, un-normalised text
    ("Akhil", "Ads", "CHANGED"),
    ("", None, "UNRESOLVED_BLANK"),
    ("Akhil", None, "UNRESOLVED_KEEPS_CURRENT"),
])
def test_change_kind(current, new, kind):
    from src.meeting_source import change_kind
    assert change_kind(current, new) == kind
