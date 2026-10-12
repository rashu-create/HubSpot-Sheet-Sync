"""Tests for the Sales Forecast tab (src/forecast.py + sheets.write_forecast_tab wiring)."""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from src import forecast
from src.forecast import (
    ForecastDeal,
    build_block,
    classify_deal,
    compose_blocks,
    month_bounds_ms,
    roll_months,
)

pytestmark = pytest.mark.real_forecast   # this file tests the real forecast code; it mocks its own I/O

NOW = datetime(2026, 10, 9, 8, 0)


def _deal(name="Acme", owner="Harsha", amount=10_000, prob=0.6, section="HIGH",
          stage="Trial", day=15, did="1", owners=None, icp="OSS", nxt=""):
    return ForecastDeal(
        id=did, name=name, owner_primary=owner, owners=owners or owner, amount=amount,
        close_date=date(2026, 10, day), stage_label=stage, stage_prob=prob, section=section,
        icp_type=icp, next_steps=nxt,
    )


# ── month rolling ─────────────────────────────────────────────────────────────

class TestRollMonths:
    def test_current_and_next(self):
        assert roll_months(date(2026, 10, 31)) == ["2026-10", "2026-11"]

    def test_first_of_next_month_rolls_forward(self):
        assert roll_months(date(2026, 11, 1)) == ["2026-11", "2026-12"]

    def test_december_rolls_into_next_year(self):
        assert roll_months(date(2026, 12, 15)) == ["2026-12", "2027-01"]

    def test_bounds_cover_full_utc_month(self):
        start, end = month_bounds_ms("2026-02")
        assert (end - start) // 1000 == 28 * 86400 - 1


# ── membership rules ──────────────────────────────────────────────────────────

class TestClassify:
    @pytest.mark.parametrize("cat,expected", [("HIGH", "HIGH"), ("medium", "MEDIUM"), ("LOW", "LOW")])
    def test_open_deal_uses_category(self, cat, expected):
        assert classify_deal("Trial", False, cat) == expected

    @pytest.mark.parametrize("cat", ["OMIT", "", None, "CLOSED"])
    def test_open_deal_without_tier_is_excluded(self, cat):
        assert classify_deal("Trial", False, cat) is None

    def test_closed_won_goes_to_closed_section_whatever_the_category(self):
        assert classify_deal("Closed Won", True, "OMIT") == "CLOSED"
        assert classify_deal("Closed Won", True, "CLOSED") == "CLOSED"

    @pytest.mark.parametrize("label", ["Closed Lost", "Irrelevant", "Pushed Out"])
    def test_closed_non_won_excluded_even_with_a_tier(self, label):
        assert classify_deal(label, True, "HIGH") is None

    def test_stage_id_named_closedwon_is_not_closed_won(self):
        """In this portal id 'closedwon' = Pricing Discussion; only the LABEL decides."""
        assert classify_deal("Pricing Discussion", False, "MEDIUM") == "MEDIUM"


# ── weighted maths ────────────────────────────────────────────────────────────

class TestWeighted:
    def test_weighted_is_amount_times_stage_probability_not_tier(self):
        # HIGH tier (80%) deal in Opportunity stage (0.4) weighs 40%, not 80%.
        assert _deal(amount=30_000, prob=0.4, section="HIGH").weighted == pytest.approx(12_000)

    def test_closed_won_counts_at_100_percent(self):
        assert _deal(amount=15_000, prob=1.0, section="CLOSED", stage="Closed Won").weighted == 15_000

    def test_reproduces_hubspot_screen_for_harsha(self):
        """Harsha's 8 deals on HubSpot's Oct-2026 screen = $100,800."""
        rows = [(30_000, .4), (15_000, .6), (18_000, .6), (20_000, .6),
                (12_000, .8), (18_000, .8), (30_000, .4), (35_000, .6)]
        total = sum(_deal(amount=a, prob=p).weighted for a, p in rows)
        assert total == pytest.approx(100_800)


# ── block layout ──────────────────────────────────────────────────────────────

def _flat(block):
    return [c for r in block.rows for c in r]


class TestBuildBlock:
    def test_totals_and_kpis(self):
        deals = [
            _deal("Won", amount=15_000, prob=1.0, section="CLOSED", stage="Closed Won", did="1"),
            _deal("A", amount=30_000, prob=.4, section="HIGH", did="2"),
            _deal("B", amount=12_000, prob=.8, section="MEDIUM", did="3"),
        ]
        b = build_block(deals, "2026-10", NOW)
        assert b.deals == 3
        assert b.closed_won == 15_000
        assert b.weighted == pytest.approx(15_000 + 12_000 + 9_600)

    def test_sections_in_order_with_biggest_first(self):
        deals = [_deal("Small", amount=1_000, did="1"), _deal("Big", amount=9_000, did="2")]
        b = build_block(deals, "2026-10", NOW)
        names = [r[0] for r in b.rows]
        assert names.index("Big") < names.index("Small")
        titles = [b.rows[row][0] for kind, row in b.styles if kind == "section"]
        assert titles == ["Closed till date", forecast.TIER_TITLE["HIGH"],
                          forecast.TIER_TITLE["MEDIUM"], forecast.TIER_TITLE["LOW"]]

    def test_rows_are_padded_to_block_width(self):
        b = build_block([_deal()], "2026-10", NOW)
        assert {len(r) for r in b.rows} == {forecast.BLOCK_WIDTH}

    def test_close_date_is_forced_to_text_so_sheets_does_not_make_it_a_serial(self):
        b = build_block([_deal(day=7)], "2026-10", NOW)
        row = next(r for r in b.rows if r[0] == "Acme")
        assert row[6] == "'7 Oct"

    def test_hyperlink_formula_points_to_the_deal(self):
        b = build_block([_deal(did="999")], "2026-10", NOW)
        assert any("record/0-3/999" in str(c) for c in _flat(b))

    def test_piyush_deals_are_included_in_the_totals(self):
        deals = [
            _deal("Harsha deal", owner="Harsha", amount=10_000, prob=.6, did="1"),
            _deal("Nasiko", owner="Piyush", amount=60_000, prob=.6, section="MEDIUM", did="2"),
        ]
        b = build_block(deals, "2026-10", NOW)
        assert b.weighted == pytest.approx(6_000 + 36_000)
        assert any(r[0] == "Piyush" for r in b.rows)

    def test_no_explanatory_note_rows(self):
        """The "Rewritten from HubSpot..." line and the Piyush footer are gone from the Overview."""
        for deals in ([_deal()], [_deal(owner="Piyush")], []):
            b = build_block(deals, "2026-10", NOW)
            text = " ".join(str(c) for c in _flat(b))
            for gone in ("Rewritten from HubSpot", "excludes Piyush", "No Piyush deals", "Sales Forecast screen"):
                assert gone not in text
            assert not any(kind == "note" for kind, _ in b.styles)

    def test_owner_table_weighted_includes_closed_won(self):
        deals = [
            _deal("Won", owner="Chandra", amount=15_000, prob=1.0, section="CLOSED", stage="Closed Won", did="1"),
            _deal("Open", owner="Chandra", amount=30_000, prob=.6, did="2"),
        ]
        b = build_block(deals, "2026-10", NOW)
        row = next(r for r in b.rows if r[0] == "Chandra" and r[6] == 2)
        assert row[4] == 15_000 and row[5] == pytest.approx(33_000)

    def test_empty_month_still_renders(self):
        b = build_block([], "2026-11", NOW)
        assert b.deals == 0 and b.weighted == 0
        assert b.rows[0][0] == "November 2026 Forecast"


class TestComposeBlocks:
    def test_blocks_sit_side_by_side_with_a_gap(self):
        a = build_block([_deal()], "2026-10", NOW)
        b = build_block([], "2026-11", NOW)
        grid, styles = compose_blocks([a, b])
        assert grid[0][0] == "October 2026 Forecast"
        second = forecast.BLOCK_WIDTH + forecast.BLOCK_GAP
        assert grid[0][second] == "November 2026 Forecast"
        assert all(len(r) == len(grid[0]) for r in grid)
        offsets = {c0 for kind, _, c0, _ in styles if kind == "title"}
        assert offsets == {0, second}


# ── fetch + orchestration (HubSpot / Sheets mocked) ───────────────────────────

_STAGES = {
    "s_trial": {"label": "Trial", "prob": 0.6, "closed": False},
    "s_won": {"label": "Closed Won", "prob": 1.0, "closed": True},
    "s_lost": {"label": "Closed Lost", "prob": 0.0, "closed": True},
}


def _raw(did, name, stage, cat, close="2026-10-15T00:00:00Z", amount="10000", owner="1"):
    return {"id": did, "properties": {
        "dealname": name, "dealstage": stage, "hs_manual_forecast_category": cat,
        "closedate": close, "amount": amount, "hubspot_owner_id": owner, "hs_all_owner_ids": owner,
    }}


class TestFetchForecastDeals:
    def _run(self, raw):
        with patch("src.forecast.hubspot._get_sales_pipeline_id", return_value="default"), \
             patch("src.forecast._fetch_stage_meta", return_value=_STAGES), \
             patch("src.forecast._search_deals", return_value=raw), \
             patch("src.forecast._fetch_company_icp", return_value={"1": "OSS"}), \
             patch("src.forecast.hubspot._get_owner_name", return_value="Harsha Agarwal"), \
             patch("src.forecast.hubspot._compute_owner_first_names", return_value="Harsha,Deeksha"):
            return forecast.fetch_forecast_deals("2026-10", "tok")

    def test_keeps_forecast_deals_and_drops_omit_lost_and_undated(self):
        raw = [
            _raw("1", "Keep", "s_trial", "HIGH"),
            _raw("2", "Omit", "s_trial", "OMIT"),
            _raw("3", "Lost", "s_lost", "HIGH"),
            _raw("4", "Won", "s_won", "CLOSED"),
            _raw("5", "NoDate", "s_trial", "HIGH", close=None),
        ]
        deals = self._run(raw)
        assert {d.name for d in deals} == {"Keep", "Won"}

    def test_maps_owner_amount_and_icp(self):
        d = self._run([_raw("1", "Keep", "s_trial", "HIGH", amount="12,500")])[0]
        assert d.owner_primary == "Harsha"
        assert d.owners == "Harsha | Deeksha"
        assert d.amount == 12_500 and d.icp_type == "OSS" and d.stage_prob == 0.6

    def test_missing_amount_is_zero_not_a_crash(self):
        assert self._run([_raw("1", "NoAmt", "s_trial", "LOW", amount=None)])[0].amount == 0

    def test_missing_pipeline_raises(self):
        with patch("src.forecast.hubspot._get_sales_pipeline_id", return_value=None):
            with pytest.raises(RuntimeError):
                forecast.fetch_forecast_deals("2026-10", "tok")


class TestRunForecastSync:
    def _patched(self, deals):
        return patch("src.forecast.fetch_forecast_deals", return_value=deals), \
               patch("src.forecast.hubspot._token", return_value="tok")

    def test_dry_run_builds_but_does_not_write(self):
        p1, p2 = self._patched([_deal()])
        with p1, p2, patch("src.sheets.write_forecast_tab") as write:
            res = forecast.run_forecast_sync(dry_run=True, today=date(2026, 10, 9))
        write.assert_not_called()
        assert res.months == ["2026-10", "2026-11"] and res.deals == 2 and not res.written

    def test_real_run_writes_only_the_two_forecast_tabs(self):
        p1, p2 = self._patched([_deal()])
        with p1, p2, patch("src.sheets.write_forecast_tab") as write:
            res = forecast.run_forecast_sync(dry_run=False, today=date(2026, 10, 9))
        assert res.written
        assert [c.args[0] for c in write.call_args_list] == ["Sales Forecast - Overview", "Sales Forecast - AEs"]

    def test_aes_tab_failure_is_labelled_and_overview_is_already_written(self):
        p1, p2 = self._patched([_deal()])
        with p1, p2, patch("src.sheets.write_forecast_tab", side_effect=[None, ValueError("boom")]) as write:
            with pytest.raises(RuntimeError, match="AEs tab: boom"):
                forecast.run_forecast_sync(dry_run=False, today=date(2026, 10, 9))
        assert write.call_args_list[0].args[0] == "Sales Forecast - Overview"

    def test_both_tabs_are_built_from_one_fetch_per_month(self):
        p1, p2 = self._patched([_deal()])
        with p1 as fetch, p2, patch("src.sheets.write_forecast_tab"):
            forecast.run_forecast_sync(dry_run=False, today=date(2026, 10, 9))
        assert fetch.call_count == 2          # Oct + Nov, not once per tab

    def test_missing_token_raises(self):
        with patch("src.forecast.hubspot._token", return_value=None):
            with pytest.raises(RuntimeError, match="HUBSPOT_API_TOKEN"):
                forecast.run_forecast_sync(dry_run=True)


# ── AEs tab ───────────────────────────────────────────────────────────────────

def _nov(**kw):
    d = _deal(**kw)
    d.close_date = date(2026, 11, kw.get("day", 15))
    return d


def _by_month(oct_deals, nov_deals=()):
    return {"2026-10": list(oct_deals), "2026-11": list(nov_deals)}


MONTHS = ["2026-10", "2026-11"]


def _sections(grid):
    """Rows that start an AE list, e.g. "Harsha's Pipeline"."""
    return [r[0] for r in grid if str(r[0]).endswith("'s Pipeline")]


class TestBuildAeGrid:
    def test_one_section_per_primary_owner_with_unowned_last(self):
        deals = _by_month(
            [_deal("A", owner="Harsha", did="1"), _deal("B", owner="", did="2", owners=""),
             _deal("C", owner="Akanksha", did="3")],
            [_nov(name="D", owner="Harsha", did="4")],
        )
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        assert _sections(grid) == ["Akanksha's Pipeline", "Harsha's Pipeline", "(unowned)'s Pipeline"]

    def test_each_deal_appears_exactly_once(self):
        deals = _by_month([_deal("A", did="1"), _deal("B", did="2")], [_nov(name="C", did="3")])
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        links = [c for r in grid for c in r if str(c).startswith("=HYPERLINK")]
        assert len(links) == 3 and len(set(links)) == 3

    def test_summary_total_reconciles_with_overview_blocks(self):
        oct_deals = [_deal("A", amount=100_000, prob=0.6, did="1"),
                     _deal("W", amount=15_000, prob=1.0, section="CLOSED", stage="Closed Won", did="2")]
        nov_deals = [_nov(name="N", owner="Chandra", amount=20_000, prob=0.4, did="3")]
        deals = _by_month(oct_deals, nov_deals)
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        total = next(r for r in grid if r[0] == "Total")
        overview = [forecast.build_block(deals[m], m, NOW) for m in MONTHS]
        assert total[1] == sum(b.deals for b in overview) == 3
        assert total[2] == overview[0].weighted and total[3] == overview[1].weighted
        assert total[4] == sum(b.closed_won for b in overview) == 15_000
        assert total[5] == sum(b.weighted for b in overview)

    def test_section_header_splits_weighted_by_month(self):
        deals = _by_month([_deal(amount=10_000, prob=0.5, did="1")], [_nov(amount=20_000, prob=0.5, did="2")])
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        section = next(r for r in grid if r[0] == "Harsha's Pipeline")
        assert section[4] == 30_000 and section[5] == 15_000
        assert section[9] == "Weighted: Oct $5,000 | Nov $10,000"

    def test_rows_sorted_month_then_closed_high_medium_low_then_amount(self):
        deals = _by_month(
            [_deal("low", section="LOW", amount=50_000, did="1"),
             _deal("small-high", section="HIGH", amount=1_000, did="2"),
             _deal("big-high", section="HIGH", amount=9_000, did="3"),
             _deal("won", section="CLOSED", stage="Closed Won", prob=1.0, amount=500, did="4")],
            [_nov(name="nov-high", section="HIGH", amount=99_000, did="5")],
        )
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        names = [r[0] for r in grid if str(r[10]).startswith("=HYPERLINK")]
        assert names == ["won", "big-high", "small-high", "low", "nov-high"]

    def test_only_closed_won_rows_are_styled_green(self):
        deals = _by_month([_deal("won", section="CLOSED", stage="Closed Won", prob=1.0, did="1"),
                           _deal("open", did="2")])
        grid, styles = forecast.build_ae_grid(deals, MONTHS, NOW)
        won_rows = [row for kind, row, _, _ in styles if kind == "won"]
        assert [grid[r][0] for r in won_rows] == ["won"]

    def test_tier_and_probability_columns(self):
        deals = _by_month([_deal(section="MEDIUM", prob=0.6, did="1"),
                           _deal("won", section="CLOSED", stage="Closed Won", prob=1.0, did="2")])
        grid, _ = forecast.build_ae_grid(deals, MONTHS, NOW)
        rows = {r[0]: r for r in grid if str(r[10]).startswith("=HYPERLINK")}
        assert (rows["Acme"][7], rows["Acme"][8]) == ("Medium", 0.6)
        assert (rows["won"][7], rows["won"][8]) == ("Closed", 1.0)

    def test_close_date_is_text_and_every_row_has_the_full_width(self):
        grid, _ = forecast.build_ae_grid(_by_month([_deal(day=7, did="1")]), MONTHS, NOW)
        deal_row = next(r for r in grid if str(r[10]).startswith("=HYPERLINK"))
        assert deal_row[6] == "'7 Oct"
        assert {len(r) for r in grid} == {forecast.AE_WIDTH}

    def test_piyush_gets_his_own_section(self):
        grid, _ = forecast.build_ae_grid(_by_month([_deal(owner="Piyush", did="1")]), MONTHS, NOW)
        assert _sections(grid) == ["Piyush's Pipeline"]

    def test_empty_forecast_renders_a_note_not_a_crash(self):
        grid, _ = forecast.build_ae_grid(_by_month([]), MONTHS, NOW)
        assert any("No forecast deals" in str(r[0]) for r in grid)
        assert _sections(grid) == []

    def test_no_rewritten_note_row(self):
        grid, styles = forecast.build_ae_grid(_by_month([_deal(did="1")]), MONTHS, NOW)
        assert "Rewritten from HubSpot" not in " ".join(str(c) for r in grid for c in r)
        assert not any(kind == "note" for kind, *_ in styles)

    def test_title_names_both_months_year_safe(self):
        grid, _ = forecast.build_ae_grid({"2026-12": [], "2027-01": []}, ["2026-12", "2027-01"], NOW)
        assert "December 2026 + January 2027" in grid[0][0]


# ── write_forecast_tab (Google Sheets writer) ─────────────────────────────────

class TestWriteForecastTab:
    def _run(self, **kw):
        import gspread
        from src import sheets
        ss, ws = MagicMock(), MagicMock(id=7, row_count=1000, col_count=26)
        ss.worksheet.return_value = ws
        client = MagicMock()
        client.open_by_key.return_value = ss
        with patch.dict("os.environ", {"PIPELINE_SHEET_ID": "sheet"}), patch("src.sheets._get_client", return_value=client):
            return sheets, ss, ws, gspread

    def test_missing_tab_gives_a_clear_error(self):
        sheets, ss, _, gspread = self._run()
        ss.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("x")
        with patch.dict("os.environ", {"PIPELINE_SHEET_ID": "sheet"}), \
             patch("src.sheets._get_client", return_value=MagicMock(open_by_key=lambda _: ss)):
            with pytest.raises(RuntimeError, match="not found in the sheet"):
                sheets.write_forecast_tab("Sales Forecast - AEs", [["x"]], [], 1, 0, [])

    def test_whole_sheet_is_calibri_10_and_no_style_overwrites_the_font(self):
        sheets, ss, ws, _ = self._run()
        styles = [("title", 0, 0, 2), ("kpi_value", 1, 0, 2), ("header", 1, 0, 2), ("section", 1, 0, 2),
                  ("subtotal", 1, 0, 2), ("won", 1, 0, 2), ("note", 1, 0, 2), ("kpi_label", 1, 0, 2)]
        with patch.dict("os.environ", {"PIPELINE_SHEET_ID": "sheet"}), \
             patch("src.sheets._get_client", return_value=MagicMock(open_by_key=lambda _: ss)):
            sheets.write_forecast_tab("T", [["a", "b"], ["c", "d"]], styles, 2, 0, [0])
        reqs = [r["repeatCell"] for r in ss.batch_update.call_args.args[0]["requests"] if "repeatCell" in r]
        base = reqs[0]
        assert base["range"] == {"sheetId": 7} and base["fields"] == "userEnteredFormat"
        assert base["cell"]["userEnteredFormat"]["textFormat"] == {"fontFamily": "Calibri", "fontSize": 10}
        for r in reqs[1:]:
            mask = r["fields"]
            assert "textFormat," not in mask + "," and not mask.endswith("textFormat")   # never the whole textFormat
            assert "fontSize" not in str(r["cell"]) and "fontFamily" not in str(r["cell"])   # base font is untouched

    def test_won_money_and_percent_requests_are_emitted(self):
        sheets, ss, ws, _ = self._run()
        grid = [["a", "b"], ["c", "d"]]
        styles = [("won", 1, 0, 2), ("money", 0, 1, 2)]
        with patch.dict("os.environ", {"PIPELINE_SHEET_ID": "sheet"}), \
             patch("src.sheets._get_client", return_value=MagicMock(open_by_key=lambda _: ss)):
            sheets.write_forecast_tab("T", grid, styles, 2, 0, [0], col_widths=[50, 60], percent_cols=[1])
        reqs = ss.batch_update.call_args.args[0]["requests"]
        fields = [r["repeatCell"]["cell"].get("userEnteredFormat", {}) for r in reqs if "repeatCell" in r]
        assert any(f.get("numberFormat", {}).get("type") == "PERCENT" for f in fields)
        assert any("foregroundColor" in f.get("textFormat", {}) for f in fields)       # won
        assert sum(1 for f in fields if f.get("numberFormat", {}).get("type") == "CURRENCY") >= 2
        widths = [r["updateDimensionProperties"]["properties"]["pixelSize"]
                  for r in reqs if "updateDimensionProperties" in r]
        assert widths == [50, 60]


# ── integration with run_sync ─────────────────────────────────────────────────

class TestSyncIntegration:
    @patch("src.sync.sheets.read_pipeline_domains", return_value=[])
    @patch("src.sync.sheets.build_sdr_map", return_value={})
    def test_forecast_failure_is_recorded_but_does_not_break_sync(self, _s, _r):
        from src.sync import run_sync
        with patch("src.sync.forecast.run_forecast_sync", side_effect=RuntimeError("boom")):
            result = run_sync(dry_run=True)
        assert any(e.startswith("Forecast: boom") for e in result.errors)
        assert result.rows_total == 0

    @patch("src.sync.sheets.read_pipeline_domains", return_value=[])
    @patch("src.sync.sheets.build_sdr_map", return_value={})
    def test_forecast_summary_lands_on_run_result(self, _s, _r):
        from src.sync import run_sync
        fake = forecast.ForecastResult(months=["2026-10"], deals=18, weighted=226_200.0, written=True)
        with patch("src.sync.forecast.run_forecast_sync", return_value=fake):
            result = run_sync(dry_run=False)
        assert result.forecast_deals == 18 and result.forecast_weighted == 226_200.0
