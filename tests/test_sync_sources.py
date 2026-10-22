"""run_sync() and the Source of meeting column (AG)."""

from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

from src.meeting_source import AnalysisData, Meeting
from src.sync import run_sync

JAN6 = date(2026, 1, 6)


def _analysis(*meetings, alias_map=None):
    return AnalysisData(meetings=list(meetings), alias_map=alias_map or {}, alias_problems=[])


def _run(rows, *, analysis, first_meetings, legacy=None, row_data=None, dry_run=False,
         analysis_error=None, dates_error=None):
    """Run run_sync with everything stubbed; returns (result, written_updates)."""
    written: list[dict] = []
    row_data = row_data or {}
    with ExitStack() as st:
        st.enter_context(patch("src.sync.sheets.read_pipeline_domains", return_value=rows))
        st.enter_context(patch("src.sync.sheets.build_sdr_map", return_value=legacy or {}))
        st.enter_context(patch(
            "src.sync.meeting_source.load_analysis",
            side_effect=analysis_error, return_value=analysis))
        st.enter_context(patch(
            "src.sync.sheets.read_pipeline_first_meetings",
            side_effect=dates_error, return_value=first_meetings))
        st.enter_context(patch(
            "src.sync.hubspot.get_row_data",
            side_effect=lambda d: ({"C": "Yes", "E": ""} if d not in row_data else row_data[d])
            if row_data.get(d, True) is not None else None))
        st.enter_context(patch(
            "src.sync.sheets.write_pipeline_rows", side_effect=lambda u: written.extend(u)))
        result = run_sync(dry_run=dry_run)
    return result, written


def _by_row(written):
    return {u["row"]: u["values"] for u in written}


def test_source_comes_from_the_meeting_matching_the_row():
    analysis = _analysis(
        Meeting("a.com", date(2026, 1, 2), "Akhil", None, ""),
        Meeting("a.com", date(2026, 1, 1), "Ads", 5, ""),     # 1 Jan + 5 days == 6 Jan -> exact
    )
    result, written = _run([(2, "a.com")], analysis=analysis, first_meetings={2: JAN6})
    assert _by_row(written)[2]["E"] == "Ads"
    assert (result.source_filled, result.source_analysis, result.source_legacy,
            result.source_unresolved) == (1, 1, 0, 0)


def test_unresolved_row_is_not_blanked():
    result, written = _run([(2, "a.com")], analysis=_analysis(), first_meetings={2: JAN6})
    assert "E" not in _by_row(written)[2]            # existing sheet value is left alone
    assert result.source_unresolved == 1 and result.source_filled == 0


def test_legacy_sheet_is_the_fallback():
    result, written = _run([(2, "b.com")], analysis=_analysis(), first_meetings={2: JAN6},
                           legacy={"b.com": "Muni"})
    assert _by_row(written)[2]["E"] == "Muni"
    assert result.source_legacy == 1


def test_source_is_written_even_when_hubspot_has_no_match():
    analysis = _analysis(Meeting("nohs.com", date(2026, 1, 6), "Referral", 0, ""))
    result, written = _run([(2, "nohs.com")], analysis=analysis, first_meetings={2: JAN6},
                           row_data={"nohs.com": None})
    assert _by_row(written) == {2: {"E": "Referral"}}     # only AG, nothing else touched
    assert result.rows_skipped == 1 and result.rows_synced == 0   # still counted as a HubSpot miss
    assert result.source_filled == 1


def test_a_failed_analysis_read_skips_the_column_and_reports_it():
    result, written = _run([(2, "a.com")], analysis=None, first_meetings={2: JAN6},
                           legacy={"a.com": "Muni"}, analysis_error=RuntimeError("quota"))
    assert "E" not in _by_row(written)[2]            # legacy is NOT used as a stand-in
    assert any(e.startswith("Source of meeting") and "quota" in e for e in result.errors)
    assert result.source_skipped is True
    assert result.rows_synced == 1                    # the rest of the sync is unaffected


def test_a_failed_date_read_skips_the_column_and_reports_it():
    analysis = _analysis(Meeting("a.com", JAN6, "Ads", 0, ""))
    result, written = _run([(2, "a.com")], analysis=analysis, first_meetings=None,
                           dates_error=RuntimeError("boom"))
    assert "E" not in _by_row(written)[2]
    assert any(e.startswith("Source of meeting") for e in result.errors)
    assert result.source_skipped is True


def test_dry_run_writes_nothing_but_still_counts():
    analysis = _analysis(Meeting("a.com", JAN6, "Ads", 0, ""))
    result, written = _run([(2, "a.com")], analysis=analysis, first_meetings={2: JAN6}, dry_run=True)
    assert written == []
    assert result.source_filled == 1


def test_alias_problems_are_reported_as_errors():
    analysis = AnalysisData(meetings=[], alias_map={}, alias_problems=["row 5 (Bad): invalid"])
    result, _ = _run([(2, "a.com")], analysis=analysis, first_meetings={2: JAN6})
    assert any("alias" in e.lower() and "row 5" in e for e in result.errors)
