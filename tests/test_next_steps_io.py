"""Next Steps sync I/O pieces: snapshot store, HubSpot deal updates, sheet cell reader, log rows."""
import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src import hubspot, next_steps_log, next_steps_state, sheets
from src.next_steps import (
    BLOCKED_EMPTY, CONFLICT, NONE, UPDATE, Decision, RowCtx, RowPlan, plan_row,
)

pytestmark = pytest.mark.real_sources      # these tests mock their own gspread clients


# ── snapshot store ───────────────────────────────────────────────────────────

def test_snapshot_round_trip_and_atomic_write(tmp_path):
    p = tmp_path / "state.json"
    snap = {"acme.com": {"deal_id": "D1", "next_steps": "Call Bob", "due_date": "2026-10-25"}}
    next_steps_state.save(snap, p)
    assert next_steps_state.load(p) == snap
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]        # no tmp file left behind


def test_missing_or_corrupt_snapshot_is_empty_not_a_crash(tmp_path):
    assert next_steps_state.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert next_steps_state.load(bad) == {}
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(["a list"]))
    assert next_steps_state.load(wrong) == {}


# ── HubSpot deal update ──────────────────────────────────────────────────────

def _resp(status, body=None):
    return httpx.Response(status, json=body or {}, request=httpx.Request("PATCH", "http://x"))


def test_push_patches_each_deal_with_only_its_properties(monkeypatch):
    monkeypatch.setenv("HUBSPOT_API_TOKEN", "tok")
    calls = []

    def fake_request(method, url, *, token, **kw):
        calls.append((method, url, kw["json"]))
        return _resp(200, {"id": "1"})

    with patch.object(hubspot, "_request", side_effect=fake_request):
        out = hubspot.push_deal_updates({"1": {"next_steps_management": "Call Bob"},
                                         "2": {"next_steps_due_date_management": "2026-10-30"}})
    assert out == {"1": (True, ""), "2": (True, "")}
    assert calls[0] == ("PATCH", "https://api.hubapi.com/crm/v3/objects/deals/1",
                        {"properties": {"next_steps_management": "Call Bob"}})
    assert calls[1][2] == {"properties": {"next_steps_due_date_management": "2026-10-30"}}


def test_one_failing_deal_does_not_stop_the_others(monkeypatch):
    monkeypatch.setenv("HUBSPOT_API_TOKEN", "tok")

    def fake_request(method, url, *, token, **kw):
        return _resp(403, {"message": "missing scope crm.objects.deals.write"}) if url.endswith("/1") else _resp(200)

    with patch.object(hubspot, "_request", side_effect=fake_request):
        out = hubspot.push_deal_updates({"1": {"next_steps_management": "a"}, "2": {"next_steps_management": "b"}})
    assert out["1"][0] is False and "403" in out["1"][1] and "missing scope" in out["1"][1]
    assert out["2"] == (True, "")


def test_network_error_is_reported_per_deal(monkeypatch):
    monkeypatch.setenv("HUBSPOT_API_TOKEN", "tok")
    with patch.object(hubspot, "_request", side_effect=httpx.ConnectError("down")):
        out = hubspot.push_deal_updates({"1": {"next_steps_management": "a"}})
    assert out["1"][0] is False and "down" in out["1"][1]


def test_missing_token_fails_every_deal_without_calling_hubspot(monkeypatch):
    monkeypatch.delenv("HUBSPOT_API_TOKEN", raising=False)
    with patch.object(hubspot, "_request") as req:
        out = hubspot.push_deal_updates({"1": {"next_steps_management": "a"}})
    req.assert_not_called()
    assert out["1"][0] is False


# ── sheet reader ─────────────────────────────────────────────────────────────

def test_read_next_steps_cells_returns_both_columns_per_row_unformatted(monkeypatch):
    monkeypatch.setenv("PIPELINE_SHEET_ID", "pipe")
    ws = MagicMock()
    ws.batch_get.return_value = [[["Call Bob"], [], ["x"]], [[46325], [], []]]
    ss = MagicMock()
    ss.worksheet.return_value = ws
    client = MagicMock()
    client.open_by_key.return_value = ss
    with patch.object(sheets, "_get_client", return_value=client):
        cells = sheets.read_next_steps_cells()
    assert cells == {2: ("Call Bob", 46325), 3: ("", ""), 4: ("x", "")}
    ws.batch_get.assert_called_once_with(["S2:S", "T2:T"], value_render_option="UNFORMATTED_VALUE")


# ── log rows ─────────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 23, 4, 30, 5)
TODAY = date(2026, 10, 23)
SNAP = {"deal_id": "D1", "next_steps": "Call Bob", "due_date": "2026-10-25"}


def _ctx(**kw):
    base = dict(row=5, domain="acme.com", deal_id="D1", deal_name="Acme - Reo", owner="Akanksha",
                sheet_s="Call Bob", sheet_t="2026-10-25", hs_s="Call Bob", hs_t="2026-10-25")
    base.update(kw)
    return RowCtx(**base)


def test_header_is_the_agreed_log_columns():
    assert next_steps_log.HEADER == [
        "Timestamp (UTC)", "Run ID", "Mode", "Level", "Domain", "Deal ID", "Deal Name", "Owner", "Field",
        "Sheet Value", "HubSpot Before", "Value Written", "Result", "Detail"]


def test_untouched_rows_are_not_logged_only_the_summary():
    plans = [plan_row(_ctx(), SNAP, TODAY)]
    rows = next_steps_log.build_rows("run1", "on", plans, {}, NOW, duration_s=3.2)
    assert len(rows) == 1 and rows[0][12] == "SUMMARY"
    assert "rows=1" in rows[0][13] and "duration=3s" in rows[0][13]


def test_an_updated_field_is_logged_with_before_and_written_values():
    plan = plan_row(_ctx(sheet_s="Send pricing"), SNAP, TODAY)
    rows = next_steps_log.build_rows("run1", "on", [plan], {"D1": (True, "")}, NOW)
    r = rows[0]
    assert r[:4] == ["2026-10-23 04:30:05", "run1", "on", "INFO"]
    assert r[4:9] == ["acme.com", "D1", "Acme - Reo", "Akanksha", "next_steps"]
    assert (r[9], r[10], r[11], r[12]) == ("Send pricing", "Call Bob", "Send pricing", "UPDATED")


def test_dry_run_logs_would_update_and_writes_nothing():
    plan = plan_row(_ctx(sheet_s="Send pricing"), SNAP, TODAY)
    r = next_steps_log.build_rows("run1", "dry_run", [plan], {}, NOW)[0]
    assert r[12] == "WOULD_UPDATE" and r[11] == ""


def test_a_failed_push_is_an_error_row_with_the_detail():
    plan = plan_row(_ctx(sheet_s="Send pricing"), SNAP, TODAY)
    r = next_steps_log.build_rows("run1", "on", [plan], {"D1": (False, "403 missing scope")}, NOW)[0]
    assert (r[3], r[12]) == ("ERROR", "ERROR") and "403 missing scope" in r[13]


def test_conflict_and_blocked_rows_are_warnings_that_show_the_restored_hubspot_value():
    conflict = plan_row(_ctx(sheet_s="mine", hs_s="theirs"), SNAP, TODAY)
    blocked = plan_row(_ctx(row=6, domain="b.com", deal_id="D1", sheet_s="", hs_s="Call Bob"), SNAP, TODAY)
    rows = next_steps_log.build_rows("run1", "on", [conflict, blocked], {}, NOW)
    by_result = {r[12]: r for r in rows if r[12] != "SUMMARY"}
    assert by_result[CONFLICT][3] == "WARN" and by_result[CONFLICT][11] == "theirs"
    assert by_result[BLOCKED_EMPTY][3] == "WARN" and by_result[BLOCKED_EMPTY][11] == "Call Bob"


def test_skips_are_logged_only_when_the_sheet_differs_from_the_snapshot():
    quiet = plan_row(_ctx(deal_id=""), SNAP, TODAY)                              # SKIPPED_NO_DEAL, cell unchanged
    loud = plan_row(_ctx(deal_id="", sheet_s="edited"), SNAP, TODAY)             # AE edited a row we can't push
    assert [r for r in next_steps_log.build_rows("r", "on", [quiet], {}, NOW, snapshot={"acme.com": SNAP})
            if r[12] != "SUMMARY"] == []
    rows = [r for r in next_steps_log.build_rows("r", "on", [loud], {}, NOW, snapshot={"acme.com": SNAP})
            if r[12] != "SUMMARY"]
    assert rows and rows[0][12] == "SKIPPED_NO_DEAL"


def test_summary_counts_every_outcome():
    plans = [
        plan_row(_ctx(row=2, domain="a.com", sheet_s="x"), SNAP, TODAY),                 # update
        plan_row(_ctx(row=3, domain="b.com", sheet_s="mine", hs_s="theirs"), SNAP, TODAY),  # conflict
        plan_row(_ctx(row=4, domain="c.com", sheet_s=""), SNAP, TODAY),                  # blocked
        plan_row(_ctx(row=5, domain="d.com"), None, TODAY),                              # baseline x2 fields
    ]
    summary = next_steps_log.build_rows("r", "on", plans, {"D1": (True, "")}, NOW)[-1]
    detail = summary[13]
    for token in ("rows=4", "updated=1", "conflicts=1", "blocked=1", "baseline=1"):
        assert token in detail, detail


class FakeLogWs:
    def __init__(self, first_row=None, fail_times=0):
        self.first_row, self.fail_times, self.appended, self.header_written = first_row or [], fail_times, [], None

    def row_values(self, n):
        return self.first_row

    def update(self, values=None, range_name=None, **kw):
        self.header_written = (range_name, values)

    def append_rows(self, rows, **kw):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("boom")
        self.appended.extend(rows)


def _log_client(ws):
    ss = MagicMock()
    ss.worksheet.return_value = ws
    client = MagicMock()
    client.open_by_key.return_value = ss
    return client


def test_append_writes_the_header_once_when_the_tab_is_empty(monkeypatch):
    monkeypatch.setenv("NEXT_STEPS_LOG_SHEET_ID", "logsheet")
    ws = FakeLogWs()
    with patch.object(sheets, "_get_client", return_value=_log_client(ws)):
        next_steps_log.append([["a"] * 14])
    assert ws.header_written[1] == [next_steps_log.HEADER] and ws.appended == [["a"] * 14]
    ws2 = FakeLogWs(first_row=next_steps_log.HEADER)
    with patch.object(sheets, "_get_client", return_value=_log_client(ws2)):
        next_steps_log.append([["b"] * 14])
    assert ws2.header_written is None


def test_append_retries_once_then_raises(monkeypatch):
    monkeypatch.setenv("NEXT_STEPS_LOG_SHEET_ID", "logsheet")
    with patch.object(sheets, "_get_client", return_value=_log_client(FakeLogWs(first_row=["x"], fail_times=1))), \
         patch("src.next_steps_log.time.sleep"):
        next_steps_log.append([["a"] * 14])                                   # second attempt succeeds
    ws = FakeLogWs(first_row=["x"], fail_times=2)
    with patch.object(sheets, "_get_client", return_value=_log_client(ws)), patch("src.next_steps_log.time.sleep"):
        with pytest.raises(RuntimeError):
            next_steps_log.append([["a"] * 14])


def test_append_without_a_sheet_id_raises(monkeypatch):
    monkeypatch.delenv("NEXT_STEPS_LOG_SHEET_ID", raising=False)
    with pytest.raises(RuntimeError, match="NEXT_STEPS_LOG_SHEET_ID"):
        next_steps_log.append([["a"] * 14])
