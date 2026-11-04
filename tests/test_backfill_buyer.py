"""scripts/backfill_buyer_designation.py: the planning rules (pure) - what gets written to HubSpot and what never does."""
import importlib.util
from pathlib import Path

import pytest

from src.manager_sheet import ManagerRow

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_buyer_designation.py"


@pytest.fixture(scope="module")
def bf():
    spec = importlib.util.spec_from_file_location("backfill_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def deal(did, name, company="", closed=False, buyer=""):
    return {"id": did, "name": name, "company": company or name, "closed": closed, "buyer": buyer}


def mrow(name, buyer, row=3, meetings=1):
    return ManagerRow(name=name, buyer=buyer, meetings=meetings, row=row)


def one(bf, rows, deals):
    return {p["manager_name"]: p for p in bf.plan(rows, deals)}


class TestPlan:
    def test_blank_buyer_in_hubspot_is_written(self, bf):
        p = one(bf, [mrow("WarpBuild", "CEO")], [deal("1", "WarpBuild")])["WarpBuild"]
        assert (p["action"], p["deal_id"], p["new_value"]) == ("WRITE", "1", "CEO")

    def test_existing_hubspot_value_is_never_overwritten(self, bf):
        p = one(bf, [mrow("WarpBuild", "CEO")], [deal("1", "WarpBuild", buyer="CTO")])["WarpBuild"]
        assert p["action"] == "SKIP_HAS_VALUE" and p["current_value"] == "CTO"

    def test_whitespace_only_hubspot_value_counts_as_blank(self, bf):
        p = one(bf, [mrow("WarpBuild", "CEO")], [deal("1", "WarpBuild", buyer="  ")])["WarpBuild"]
        assert p["action"] == "WRITE"

    @pytest.mark.parametrize("text", ["Not Identified", "Not identified", "  not identified ", "NOT IDENTIFIED."])
    def test_not_identified_is_skipped(self, bf, text):
        p = one(bf, [mrow("Posit", text)], [deal("1", "Posit")])["Posit"]
        assert p["action"] == "SKIP_NOT_IDENTIFIED"

    def test_rows_without_a_buyer_are_left_out_entirely(self, bf):
        assert bf.plan([mrow("Fastino", "")], [deal("1", "Fastino")]) == []

    def test_no_matching_deal(self, bf):
        p = one(bf, [mrow("Ghost Co", "CEO")], [deal("1", "WarpBuild")])["Ghost Co"]
        assert p["action"] == "NO_MATCH" and p["deal_id"] == ""

    def test_company_name_matches_even_if_the_deal_title_differs(self, bf):
        p = one(bf, [mrow("Agora", "CEO")], [deal("7", "Agora - Reo POC 2026", company="Agora")])["Agora"]
        assert p["action"] == "WRITE" and p["deal_id"] == "7"

    def test_falls_back_to_the_deal_name(self, bf):
        p = one(bf, [mrow("Solo", "CRO")], [deal("8", "Solo", company="Solo.io Holdings")])["Solo"]
        assert p["action"] == "WRITE" and p["deal_id"] == "8"

    def test_spacing_difference_still_matches(self, bf):
        p = one(bf, [mrow("VirtuosoQA", "CEO")], [deal("9", "Virtuoso QA")])["VirtuosoQA"]
        assert p["action"] == "WRITE" and p["deal_id"] == "9"

    def test_combined_manager_name_matches_either_part(self, bf):
        p = one(bf, [mrow("Bryntum | AG Grid", "CEO")], [deal("3", "Bryntum")])["Bryntum | AG Grid"]
        assert p["action"] == "WRITE" and p["deal_id"] == "3"

    def test_several_deals_the_single_open_one_is_chosen(self, bf):
        deals = [deal("1", "Acme", closed=True), deal("2", "Acme", closed=False)]
        p = one(bf, [mrow("Acme", "CEO")], deals)["Acme"]
        assert p["action"] == "WRITE" and p["deal_id"] == "2"

    def test_two_open_deals_is_ambiguous_and_nothing_is_written(self, bf):
        deals = [deal("1", "Acme"), deal("2", "Acme")]
        p = one(bf, [mrow("Acme", "CEO")], deals)["Acme"]
        assert p["action"] == "AMBIGUOUS" and p["deal_id"] == ""

    def test_all_closed_with_several_deals_is_ambiguous(self, bf):
        deals = [deal("1", "Acme", closed=True), deal("2", "Acme", closed=True)]
        assert one(bf, [mrow("Acme", "CEO")], deals)["Acme"]["action"] == "AMBIGUOUS"

    def test_a_single_closed_deal_is_still_written(self, bf):
        p = one(bf, [mrow("Acme", "CEO")], [deal("1", "Acme", closed=True)])["Acme"]
        assert p["action"] == "WRITE"

    def test_two_manager_rows_for_one_deal_first_wins(self, bf):
        rows = [mrow("Acme", "CEO", row=5), mrow("Acme Inc", "CTO", row=60)]
        plan = bf.plan(rows, [deal("1", "Acme")])
        assert [(p["row"], p["action"]) for p in plan] == [(5, "WRITE"), (60, "SKIP_DUPLICATE_ROW")]

    def test_never_more_than_one_write_per_deal(self, bf):
        rows = [mrow("Acme", "CEO", row=5), mrow("Acme", "CTO", row=60), mrow("Other", "CFO", row=6)]
        plan = bf.plan(rows, [deal("1", "Acme"), deal("2", "Other")])
        writes = [p["deal_id"] for p in plan if p["action"] == "WRITE"]
        assert sorted(writes) == ["1", "2"]

    def test_short_names_do_not_match_longer_ones(self, bf):
        p = one(bf, [mrow("Qt", "CEO")], [deal("1", "Qualcomm Incorporated")])["Qt"]
        assert p["action"] == "NO_MATCH"

    def test_buyer_text_is_trimmed_before_writing(self, bf):
        p = one(bf, [mrow("Acme", "  Head of Sales \n")], [deal("1", "Acme")])["Acme"]
        assert p["new_value"] == "Head of Sales"


class TestSummaryAndUpdates:
    def test_updates_contain_only_write_rows(self, bf):
        rows = [mrow("A", "CEO"), mrow("B", "Not identified"), mrow("C", "CTO")]
        deals = [deal("1", "A"), deal("2", "B"), deal("3", "C", buyer="x")]
        assert bf.updates_from(bf.plan(rows, deals)) == {"1": {"buyer_designation": "CEO"}}

    def test_summary_counts_every_action(self, bf):
        rows = [mrow("A", "CEO"), mrow("B", "Not identified"), mrow("Z", "CEO")]
        counts = bf.summarize(bf.plan(rows, [deal("1", "A"), deal("2", "B")]))
        assert counts == {"WRITE": 1, "SKIP_NOT_IDENTIFIED": 1, "NO_MATCH": 1}

    def test_apply_refuses_when_the_write_count_differs_from_the_approved_one(self, bf):
        plan = bf.plan([mrow("A", "CEO")], [deal("1", "A")])
        with pytest.raises(SystemExit, match="expected 5"):
            bf.check_expected(plan, expected=5)
        bf.check_expected(plan, expected=1)             # the approved number passes
