"""Next Steps / Due Date two-way sync: normalisation and the per-field / per-row decisions (pure logic)."""
from datetime import date, timedelta

import pytest

from src.next_steps import (
    BASELINE, BLOCKED_EMPTY, BLOCKED_INVALID_DATE, CONFLICT, NONE, SKIPPED_DEAL_CHANGED, SKIPPED_DUPLICATE_ROW,
    SKIPPED_NO_DEAL, UPDATE, RowCtx, decide_field, norm_date, norm_text, plan_row,
)

TODAY = date(2026, 10, 23)


def serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


# ── normalisation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("   ", ""), ("  call Bob  ", "call Bob"),
    ("a\r\nb", "a\nb"), ("a\rb", "a\nb"), (5, "5"), (5.0, "5"), ("05", "05"),
])
def test_norm_text(raw, expected):
    assert norm_text(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("  ", ""),
    ("2026-10-30", "2026-10-30"),
    ("2026-10-30T00:00:00Z", "2026-10-30"),
    ("2026-10-30T00:00:00.000+00:00", "2026-10-30"),
    (serial(date(2026, 10, 30)), "2026-10-30"),
    (float(serial(date(2026, 10, 30))), "2026-10-30"),
    ("30 Oct 2026", "2026-10-30"),
    ("30 October 2026", "2026-10-30"),
    ("30/10/2026", "2026-10-30"),
    (1792108800000, "2026-10-16"),            # HubSpot epoch milliseconds (UTC midnight)
    ("1792108800000", "2026-10-16"),
    ("TBD", None), ("next week", None), (True, None), ("2026-13-45", None), (0, None),
])
def test_norm_date(raw, expected):
    assert norm_date(raw) == expected


# ── decide_field: next steps (free text) ─────────────────────────────────────

def ns(sheet, base, hs):
    return decide_field("next_steps", sheet, base, hs, TODAY)


def test_no_snapshot_yet_means_baseline_never_a_push():
    d = ns("anything", None, "other")
    assert d.action == BASELINE and d.push is None


def test_sheet_equal_to_snapshot_is_no_edit():
    assert ns("Call Bob", "Call Bob", "Call Bob").action == NONE
    assert ns("Call Bob", "Call Bob", "changed in HubSpot").action == NONE   # HubSpot-side change flows down


def test_edit_with_hubspot_unchanged_is_pushed():
    d = ns("Send pricing", "Call Bob", "Call Bob")
    assert d.action == UPDATE and d.push == "Send pricing"


def test_edit_when_hubspot_also_changed_is_a_conflict_and_hubspot_wins():
    d = ns("Send pricing", "Call Bob", "Book demo")
    assert d.action == CONFLICT and d.push is None


def test_edit_that_already_equals_hubspot_needs_no_push():
    assert ns("Book demo", "Call Bob", "Book demo").action == NONE


def test_clearing_the_cell_is_blocked():
    for blank in ("", "   ", None):
        d = ns(blank, "Call Bob", "Call Bob")
        assert d.action == BLOCKED_EMPTY and d.push is None


def test_blank_to_blank_is_no_edit():
    assert ns("", "", "").action == NONE


def test_typing_into_an_empty_field_is_pushed():
    assert ns("First note", "", "").action == UPDATE


def test_line_ending_and_whitespace_differences_are_not_edits():
    assert ns("line1\r\nline2 ", "line1\nline2", "line1\nline2").action == NONE


# ── decide_field: due date ───────────────────────────────────────────────────

def due(sheet, base, hs):
    return decide_field("due_date", sheet, base, hs, TODAY)


def test_valid_new_date_is_pushed_as_iso():
    d = due(serial(date(2026, 10, 30)), "2026-10-25", "2026-10-25")
    assert d.action == UPDATE and d.push == "2026-10-30"


def test_same_day_in_another_format_is_not_an_edit():
    assert due("25 Oct 2026", "2026-10-25", "2026-10-25").action == NONE
    assert due(serial(date(2026, 10, 25)), "2026-10-25", "2026-10-25T00:00:00Z").action == NONE


def test_unparseable_date_is_blocked():
    d = due("next week", "2026-10-25", "2026-10-25")
    assert d.action == BLOCKED_INVALID_DATE and d.push is None


@pytest.mark.parametrize("offset,action", [(-31, BLOCKED_INVALID_DATE), (-30, UPDATE), (365, UPDATE),
                                           (366, BLOCKED_INVALID_DATE)])
def test_date_window_is_today_minus_30_to_plus_365(offset, action):
    assert due((TODAY + timedelta(days=offset)).isoformat(), "2026-01-01", "2026-01-01").action == action


def test_clearing_the_date_is_blocked():
    assert due("", "2026-10-25", "2026-10-25").action == BLOCKED_EMPTY


def test_date_conflict_hubspot_wins():
    assert due("2026-10-30", "2026-10-25", "2026-11-02").action == CONFLICT


# ── plan_row ─────────────────────────────────────────────────────────────────

def ctx(**kw):
    base = dict(row=5, domain="acme.com", deal_id="D1", deal_name="Acme", owner="Akanksha",
                sheet_s="Call Bob", sheet_t=serial(date(2026, 10, 25)), hs_s="Call Bob", hs_t="2026-10-25")
    base.update(kw)
    return RowCtx(**base)


SNAP = {"deal_id": "D1", "next_steps": "Call Bob", "due_date": "2026-10-25"}


def test_untouched_row_does_nothing():
    p = plan_row(ctx(), SNAP, TODAY)
    assert [d.action for d in p.decisions] == [NONE, NONE]
    assert p.push == {} and p.keep == set()


def test_both_fields_edited_are_pushed_with_the_hubspot_property_names():
    p = plan_row(ctx(sheet_s="Send pricing", sheet_t="2026-10-30"), SNAP, TODAY)
    assert p.push == {"next_steps_management": "Send pricing", "next_steps_due_date_management": "2026-10-30"}
    assert p.keep == {"next_steps", "due_date"}          # the AE's cells are left as typed


def test_one_valid_field_is_pushed_while_the_invalid_one_is_restored():
    p = plan_row(ctx(sheet_s="Send pricing", sheet_t="soon"), SNAP, TODAY)
    assert p.push == {"next_steps_management": "Send pricing"}
    assert p.keep == {"next_steps"}                       # due date is rewritten from HubSpot
    assert {d.field: d.action for d in p.decisions} == {"next_steps": UPDATE, "due_date": BLOCKED_INVALID_DATE}


def test_no_snapshot_for_the_row_builds_a_baseline_and_pushes_nothing():
    p = plan_row(ctx(sheet_s="whatever"), None, TODAY)
    assert [d.action for d in p.decisions] == [BASELINE, BASELINE] and p.push == {} and p.keep == set()


def test_changed_deal_resets_the_baseline():
    p = plan_row(ctx(sheet_s="Send pricing"), {**SNAP, "deal_id": "OLD"}, TODAY)
    assert p.skip == SKIPPED_DEAL_CHANGED and p.push == {} and p.keep == set()


def test_row_without_a_deal_is_skipped():
    p = plan_row(ctx(deal_id=""), SNAP, TODAY)
    assert p.skip == SKIPPED_NO_DEAL and p.push == {}


def test_duplicate_domain_rows_are_skipped():
    p = plan_row(ctx(), SNAP, TODAY, duplicate=True)
    assert p.skip == SKIPPED_DUPLICATE_ROW and p.push == {}
