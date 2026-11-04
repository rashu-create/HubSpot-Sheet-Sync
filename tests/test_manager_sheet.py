"""Manager's "Sales Pipeline - 2026" sheet reader (src/manager_sheet.py) - pure parsing, no network."""

import pytest

from src import manager_sheet as ms


def _grid():
    """A tiny copy of the manager's Nov'26 tab: an older block on the left, the live block on the right."""
    top = ["", "October Pipeline", "", "", "", "", "November Pipeline", "", "", "", "", ""]
    hdr = ["Total", "Type", "Owner", "Status", "", "Total", "Type", "Next Steps", "Cohort",
           "Champion & Designation", "Buyer & Designation", "# of Meetings"]
    return [
        top,
        hdr,
        ["Old Co", "OSS", "X", "Closed", "$1", "WarpBuild", "Closed Source", "n", "SMB", "Head of Growth", "CEO", "4"],
        ["", "", "", "", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "High Probability - 80% closure chances", "", "", "", "", "", ""],
        ["", "", "", "", "", "Akamai", "", "", "Enterprise", "x", "VP Digital Tech, Insights & Ops", "11"],
        ["", "", "", "", "", "Posit", "", "", "", "", "Not Identified", "4"],
        ["", "", "", "", "", "", "", "", "", "", "", "1"],                      # stray count, no company
        ["", "", "", "", "", "Jobrunr", "", "", "", "", "CEO", ""],               # no meetings yet
        ["", "", "", "", "", "December Pipeline", "", "", "", "", "", ""],
        ["", "", "", "", "", "Bryntum | AG Grid", "", "", "", "", "CEO - Bryntum", "1"],
        ["", "", "", "", "", "Fastino", "", "", "", "", "", "4+"],                # non-numeric text is kept
        ["", "", "", "", "", "WarpBuild", "", "", "", "", "COO", "9"],            # duplicate: first one wins
    ]


class TestKey:
    @pytest.mark.parametrize("a,b", [("Dify AI", "dify"), ("Stream Native", "streamnative"),
                                     ("Qodo.ai", "qodo"), ("Acme, Inc.", "acme")])
    def test_normalises_case_punctuation_and_corporate_suffixes(self, a, b):
        assert ms.key(a) == ms.key(b)

    def test_names_that_differ_only_by_a_space_still_share_a_key(self):
        """HubSpot says "Virtuoso QA", the manager's sheet says "VirtuosoQA" (live miss on 2026-11-04)."""
        assert set(ms.name_keys("Virtuoso QA")) & set(ms.name_keys("VirtuosoQA"))
        assert set(ms.name_keys("Stream Native")) & set(ms.name_keys("StreamNative"))

    def test_different_companies_do_not_share_a_key(self):
        assert not set(ms.name_keys("Qt")) & set(ms.name_keys("Qualcomm"))
        assert not set(ms.name_keys("Posit")) & set(ms.name_keys("Positron"))

    def test_blank_is_blank(self):
        assert ms.key("") == "" and ms.key(None) == ""


class TestParseBlock:
    def test_reads_the_rightmost_block_with_meetings(self):
        rows = ms.parse_block(_grid())
        first = next(r for r in rows if r.name == "WarpBuild")
        assert first.meetings == 4 and first.buyer == "CEO"
        assert "Old Co" not in {r.name for r in rows}                  # not the left-hand October block

    def test_section_headings_and_stray_rows_are_skipped(self):
        names = [r.name for r in ms.parse_block(_grid())]
        assert not any("Probability" in n or "Pipeline" in n or n == "Total" for n in names)
        assert "" not in names

    def test_december_section_is_included(self):
        assert {"Bryntum | AG Grid", "Fastino"} <= {r.name for r in ms.parse_block(_grid())}

    def test_blank_meetings_is_none_and_text_is_kept(self):
        rows = {r.name: r for r in ms.parse_block(_grid())}
        assert rows["Jobrunr"].meetings is None
        assert rows["Fastino"].meetings == "4+"

    def test_first_occurrence_of_a_company_wins(self):
        rows = ms.parse_block(_grid())
        assert [r.meetings for r in rows if r.name == "WarpBuild"] == [4, 9]    # parse keeps both...
        assert ms.meetings_by_key(rows)[ms.key("WarpBuild")] == 4                # ...the lookup uses the first

    def test_combined_names_match_each_part(self):
        m = ms.meetings_by_key(ms.parse_block(_grid()))
        assert m[ms.key("Bryntum")] == 1 and m[ms.key("AG Grid")] == 1

    def test_missing_meetings_header_is_a_clear_error(self):
        with pytest.raises(ms.ManagerSheetError, match="# of Meetings"):
            ms.parse_block([["Total", "Type"], ["Acme", "OSS"]])

    def test_header_found_by_name_not_position(self):
        shifted = [[""] * 3 + r for r in _grid()]
        assert {r.name: r.meetings for r in ms.parse_block(shifted)}["Akamai"] == 11


class TestPickTab:
    TITLES = ["US Meetings", "Jul-Aug'26 Pipeline", "Nov'26 Pipeline", "Sales Pipeline 2026", "Sales Pipeline 2025"]

    def test_newest_month_tab_is_chosen(self):
        assert ms.pick_tab(self.TITLES + ["Dec'26 Pipeline"]) == "Dec'26 Pipeline"

    def test_the_year_beats_the_month(self):
        assert ms.pick_tab(["Dec'26 Pipeline", "Jan'27 Pipeline"]) == "Jan'27 Pipeline"

    def test_only_one_month_tab_is_fine_and_range_tabs_are_ignored(self):
        assert ms.pick_tab(self.TITLES) == "Nov'26 Pipeline"

    def test_override_wins_when_it_exists(self):
        assert ms.pick_tab(self.TITLES, override="Jul-Aug'26 Pipeline") == "Jul-Aug'26 Pipeline"

    def test_override_that_does_not_exist_is_an_error(self):
        with pytest.raises(ms.ManagerSheetError, match="not found"):
            ms.pick_tab(self.TITLES, override="Nope")

    def test_no_month_tab_is_an_error(self):
        with pytest.raises(ms.ManagerSheetError):
            ms.pick_tab(["US Meetings"])
