"""Sheet I/O for Source of meeting: legacy sdr map fixes, pipeline dates, analysis loader."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src import meeting_source, sheets
from src.meeting_source import Meeting

pytestmark = pytest.mark.real_sources


class FakeTab:
    def __init__(self, title, values):
        self.title = title
        self._values = values

    def get_all_values(self):
        return self._values

    def get(self, rng, value_render_option=None):
        self.last_call = (rng, value_render_option)
        return self._values


def _client(tabs, key_to_tabs=None):
    ss = MagicMock()
    ss.worksheets.return_value = tabs
    ss.worksheet.side_effect = lambda name: next(t for t in tabs if t.title == name)
    client = MagicMock()
    client.open_by_key.return_value = ss
    return client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SOURCE_SHEET_ID", "src123")
    monkeypatch.setenv("PIPELINE_SHEET_ID", "pipe123")
    monkeypatch.setenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026")
    monkeypatch.setenv("ANALYSIS_SHEET_ID", "ana123")


# ── legacy build_sdr_map fixes ───────────────────────────────────────────────

def test_legacy_map_reads_tabs_headed_source_of_meeting_not_only_sdr():
    tabs = [
        FakeTab("old", [["Company", "SDR"], ["a.com", "akhil"]]),
        FakeTab("new", [["Company", "Meeting Date", "Source of Meeting"], ["b.com", "6 Jun", "Ads"]]),
    ]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        got = sheets.build_sdr_map()
    assert got == {"a.com": "Akhil", "b.com": "Ads"}


def test_legacy_map_prefers_a_domain_header_over_an_earlier_company_name_column():
    tabs = [FakeTab("t", [["Company Name", "Domain", "SDR"], ["Acme Inc", "acme.com", "Muni"]])]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        assert sheets.build_sdr_map() == {"acme.com": "Muni"}


def test_legacy_map_a_blank_source_never_overwrites_a_value():
    tabs = [
        FakeTab("old", [["Company", "SDR"], ["a.com", "Akhil"]]),
        FakeTab("new", [["Company", "SDR"], ["a.com", ""]]),
    ]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        assert sheets.build_sdr_map() == {"a.com": "Akhil"}


def test_legacy_map_later_tab_wins_when_it_has_a_value():
    tabs = [
        FakeTab("old", [["Company", "SDR"], ["a.com", "Akhil"]]),
        FakeTab("new", [["Company", "SDR"], ["a.com", "Rama"]]),
    ]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        assert sheets.build_sdr_map() == {"a.com": "Rama"}


def test_legacy_map_drops_placeholder_sources_and_normalises_the_rest():
    tabs = [FakeTab("t", [["Company", "SDR"], ["a.com", "?"], ["b.com", "ads / inbound"], ["c.com", "nan"]])]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        assert sheets.build_sdr_map() == {"b.com": "Ads"}


def test_legacy_map_keys_follow_the_alias_map():
    tabs = [FakeTab("t", [["Company", "SDR"], ["splunk.com", "Muni"]])]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        assert sheets.build_sdr_map({"splunk.com": "cisco.com"}) == {"cisco.com": "Muni"}


# ── pipeline first-meeting dates ─────────────────────────────────────────────

def test_read_pipeline_first_meetings_maps_row_number_to_date():
    tab = FakeTab("Sales Pipeline 2026", [[46027], [46028], [], ["TBD"], ["6 Jan 2026"]])
    with patch.object(sheets, "_get_client", return_value=_client([tab])):
        got = sheets.read_pipeline_first_meetings()
    assert got == {2: date(2026, 1, 5), 3: date(2026, 1, 6), 4: None, 5: None, 6: date(2026, 1, 6)}
    assert tab.last_call == ("B2:B", "UNFORMATTED_VALUE")


# ── analysis loader ──────────────────────────────────────────────────────────

DATA = [
    ["Domain", "Meeting Date", "Source of Meeting", "Days Late", "Rebooking of"],
    ["acme.com", 46028, "Ads", 2, ""],
]
ALIASES = [
    ["Company Name", "Primary Domain", "Domain 1", "Domain 2", "Domain 3", "Total Domains",
     "Relationship", "Redirects", "M&A / Notes", "Source", "Status"],
    ["Cisco", "cisco.com", "splunk.com", "", "", "2", "", "", "", "", "Active"],
    ["Bad", "not a domain", "x.io", "", "", "", "", "", "", "", "Active"],
]


def test_load_analysis_reads_data_and_validated_aliases():
    tabs = [FakeTab("Data", DATA), FakeTab("Domain Aliases", ALIASES)]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        got = meeting_source.load_analysis()
    assert got.meetings == [Meeting("acme.com", date(2026, 1, 6), "Ads", 2, "")]
    assert got.alias_map == {"splunk.com": "cisco.com"}
    assert len(got.alias_problems) == 1
    assert tabs[0].last_call[1] == "UNFORMATTED_VALUE"


def test_load_analysis_survives_an_unreadable_alias_tab():
    data_tab = FakeTab("Data", DATA)
    ss = MagicMock()
    def worksheet(name):
        if name == "Data":
            return data_tab
        raise RuntimeError("quota")
    ss.worksheet.side_effect = worksheet
    client = MagicMock()
    client.open_by_key.return_value = ss
    with patch.object(sheets, "_get_client", return_value=client):
        got = meeting_source.load_analysis()
    assert len(got.meetings) == 1 and got.alias_map == {}
    assert any("Domain Aliases" in p for p in got.alias_problems)


def test_load_analysis_needs_the_sheet_id(monkeypatch):
    monkeypatch.delenv("ANALYSIS_SHEET_ID")
    with pytest.raises(RuntimeError, match="ANALYSIS_SHEET_ID"):
        meeting_source.load_analysis()


def test_load_analysis_fails_loudly_when_the_data_tab_changes_shape():
    tabs = [FakeTab("Data", [["Domain", "Meeting Date"]]), FakeTab("Domain Aliases", ALIASES)]
    with patch.object(sheets, "_get_client", return_value=_client(tabs)):
        with pytest.raises(ValueError, match="missing header"):
            meeting_source.load_analysis()
