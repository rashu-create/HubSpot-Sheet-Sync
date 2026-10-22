"""run_sync() with the Next Steps / Due Date write-back: modes, safety rules and the snapshot, end to end."""
import json
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

import pytest

from src.sync import run_sync

TODAY = date.today()


def serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


OLD_ISO = (TODAY).isoformat()
OLD_SERIAL = serial(TODAY)
OLD_FMT = f"{TODAY.day} {TODAY.strftime('%b')} {TODAY.year}"
SNAP = {"acme.com": {"deal_id": "D1", "next_steps": "Call Bob", "due_date": OLD_ISO}}


def hs_row(s="Call Bob", t=OLD_ISO, deal_id="D1"):
    fmt_t = OLD_FMT if t == OLD_ISO else t
    return {"C": "Yes", "D": "Akanksha", "S": s, "T": fmt_t, "_deal_id": deal_id, "_deal_name": "Acme - Reo",
            "_hs_s": s, "_hs_t": t}


class Env:
    """Everything run_sync touches, stubbed; collects what was written/pushed/logged."""

    def __init__(self, tmp_path, monkeypatch, mode):
        self.state = tmp_path / "state.json"
        monkeypatch.setenv("NEXT_STEPS_WRITEBACK", mode)
        monkeypatch.setenv("NEXT_STEPS_STATE_FILE", str(self.state))
        self.written, self.pushed, self.logged = [], [], []

    def snapshot(self):
        return json.loads(self.state.read_text()) if self.state.exists() else None

    def preload(self, snap):
        self.state.write_text(json.dumps(snap))

    def written_values(self, row=2):
        merged = {}
        for u in self.written:
            if u["row"] == row:
                merged.update(u["values"])
        return merged

    def run(self, *, rows=((2, "acme.com"),), reads, hubspot=None, push=None, header_ok=True, dry_run=False,
            read_error=None, log_error=None):
        hubspot = hubspot or {"acme.com": hs_row()}
        reads = list(reads)
        header = None if header_ok else ["x"] * 45
        from tests.test_header_guard import LIVE_HEADERS
        with ExitStack() as st:
            st.enter_context(patch("src.sync.sheets.read_pipeline_domains", return_value=list(rows)))
            st.enter_context(patch("src.sync.sheets.build_sdr_map", return_value={}))
            st.enter_context(patch("src.sync.hubspot.get_row_data",
                                   side_effect=lambda d: hubspot.get(d.lower())))
            st.enter_context(patch("src.sync.sheets.write_pipeline_rows", side_effect=self.written.extend))
            st.enter_context(patch("src.next_steps_sync.sheets.read_pipeline_header_row",
                                   return_value=header or LIVE_HEADERS))
            st.enter_context(patch("src.next_steps_sync.sheets.read_next_steps_cells",
                                   side_effect=read_error or (lambda: reads.pop(0) if len(reads) > 1 else reads[0])))
            st.enter_context(patch("src.next_steps_sync.hubspot.push_deal_updates",
                                   side_effect=lambda u: (self.pushed.append(u), push or {k: (True, "") for k in u})[1]))
            st.enter_context(patch("src.next_steps_sync.next_steps_log.append",
                                   side_effect=log_error or self.logged.extend))
            return run_sync(dry_run=dry_run)


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    return lambda mode: Env(tmp_path, monkeypatch, mode)


def cells(s="Call Bob", t=OLD_SERIAL, row=2):
    return {row: (s, t)}


def results(env):
    return [r[12] for r in env.logged]


# ── off ──────────────────────────────────────────────────────────────────────

def test_off_does_nothing_new(make_env):
    env = make_env("off")
    result = env.run(reads=[cells()])
    assert env.pushed == [] and env.logged == [] and env.snapshot() is None
    assert env.written_values()["S"] == "Call Bob" and env.written_values()["T"] == OLD_FMT
    assert result.ns_mode == "off" and "_deal_id" not in env.written_values()


def test_unknown_mode_is_treated_as_off_and_reported(make_env):
    env = make_env("maybe")
    result = env.run(reads=[cells()])
    assert result.ns_mode == "off" and env.pushed == []
    assert any("NEXT_STEPS_WRITEBACK" in e and "maybe" in e for e in result.errors)


# ── baseline ─────────────────────────────────────────────────────────────────

def test_first_run_builds_the_baseline_and_never_pushes(make_env):
    env = make_env("on")
    result = env.run(reads=[cells("whatever the AE typed"), cells("whatever the AE typed"), cells("Call Bob")])
    assert env.pushed == []
    assert env.written_values()["S"] == "Call Bob"            # first run: HubSpot value, like today
    assert env.snapshot()["acme.com"] == {"deal_id": "D1", "next_steps": "Call Bob", "due_date": OLD_ISO,
                                          "synced_at": env.snapshot()["acme.com"]["synced_at"]}
    assert "baseline=1" in env.logged[-1][13] and result.ns_updated == 0


# ── on ───────────────────────────────────────────────────────────────────────

def test_an_ae_edit_is_pushed_kept_in_the_sheet_and_snapshotted(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing")
    result = env.run(reads=[edited, edited, edited])
    assert env.pushed == [{"D1": {"next_steps_management": "Send pricing"}}]
    values = env.written_values()
    assert "S" not in values                                  # the AE's cell is left exactly as typed
    assert values["T"] == OLD_FMT                             # the untouched field is rewritten from HubSpot
    assert env.snapshot()["acme.com"]["next_steps"] == "Send pricing"
    assert "UPDATED" in results(env) and result.ns_updated == 1 and result.ns_mode == "on"


def test_a_due_date_edit_is_pushed_as_iso(make_env):
    env = make_env("on")
    env.preload(SNAP)
    from datetime import timedelta
    target = TODAY + timedelta(days=10)
    edited = cells(t=serial(target))
    env.run(reads=[edited, edited, edited])
    assert env.pushed == [{"D1": {"next_steps_due_date_management": target.isoformat()}}]
    assert "T" not in env.written_values() and env.snapshot()["acme.com"]["due_date"] == target.isoformat()


def test_a_conflict_keeps_the_hubspot_value_and_reports_it(make_env):
    env = make_env("on")
    env.preload(SNAP)
    result = env.run(reads=[cells("Send pricing"), cells("Send pricing"), cells("Book demo")],
                     hubspot={"acme.com": hs_row(s="Book demo")})
    assert env.pushed == []
    assert env.written_values()["S"] == "Book demo"
    assert env.snapshot()["acme.com"]["next_steps"] == "Book demo"
    assert "CONFLICT_HUBSPOT_WINS" in results(env) and result.ns_conflicts == 1


def test_an_emptied_cell_is_restored_from_hubspot(make_env):
    env = make_env("on")
    env.preload(SNAP)
    result = env.run(reads=[cells(""), cells(""), cells("Call Bob")])
    assert env.pushed == [] and env.written_values()["S"] == "Call Bob"
    assert "BLOCKED_EMPTY" in results(env) and result.ns_blocked == 1


def test_an_invalid_date_is_restored_and_the_valid_text_edit_still_goes(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing", "soon")
    result = env.run(reads=[edited, edited, cells("Send pricing", OLD_SERIAL)])
    assert env.pushed == [{"D1": {"next_steps_management": "Send pricing"}}]
    assert env.written_values()["T"] == OLD_FMT and "S" not in env.written_values()
    assert "BLOCKED_INVALID_DATE" in results(env) and "UPDATED" in results(env)


def test_a_failed_push_keeps_the_edit_in_the_sheet_and_the_old_snapshot(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing")
    result = env.run(reads=[edited, edited, edited], push={"D1": (False, "403 missing scope")})
    assert "S" not in env.written_values()                    # AE's text survives, retried next run
    assert env.snapshot()["acme.com"]["next_steps"] == "Call Bob"
    assert result.ns_errors == 1 and any(e.startswith("Next steps: push failed") for e in result.errors)
    assert "ERROR" in results(env)


def test_a_cell_edited_while_the_run_was_in_flight_is_left_alone(make_env):
    env = make_env("on")
    env.preload(SNAP)
    result = env.run(reads=[cells("Send pricing"), cells("Typed after we read"), cells("Typed after we read")])
    assert env.pushed == []                                   # stale decision is dropped, picked up next run
    assert "S" not in env.written_values() and "T" not in env.written_values()
    assert env.snapshot()["acme.com"]["next_steps"] == "Call Bob"


def test_text_that_sheets_would_reinterpret_is_written_as_text(make_env):
    env = make_env("on")
    env.preload({"acme.com": {"deal_id": "D1", "next_steps": "old", "due_date": OLD_ISO}})
    env.run(reads=[cells("old"), cells("old"), cells("=SUM(1)")], hubspot={"acme.com": hs_row(s="=SUM(1)")})
    assert env.written_values()["S"] == "'=SUM(1)"


# ── dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_logs_would_update_pushes_nothing_and_keeps_the_ae_edit(make_env):
    env = make_env("dry_run")
    env.preload(SNAP)
    edited = cells("Send pricing")
    result = env.run(reads=[edited, edited, edited])
    assert env.pushed == []
    assert "S" not in env.written_values()                    # never destroys the AE's edit
    assert env.snapshot()["acme.com"]["next_steps"] == "Call Bob"
    assert "WOULD_UPDATE" in results(env) and result.ns_would_update == 1 and result.ns_mode == "dry_run"


def test_dry_run_with_no_edits_plans_nothing(make_env):
    env = make_env("dry_run")
    env.preload(SNAP)
    result = env.run(reads=[cells(), cells(), cells()])
    assert result.ns_would_update == 0 and "WOULD_UPDATE" not in results(env)


# ── guards ───────────────────────────────────────────────────────────────────

def test_a_moved_layout_switches_the_feature_off_for_the_run(make_env):
    env = make_env("on")
    env.preload(SNAP)
    result = env.run(reads=[cells("Send pricing")], header_ok=False)
    assert env.pushed == [] and env.logged == []
    assert any(e.startswith("Next steps") and "layout" in e.lower() for e in result.errors)


def test_an_unreadable_sheet_switches_the_feature_off_for_the_run(make_env):
    env = make_env("on")
    env.preload(SNAP)
    result = env.run(reads=[], read_error=RuntimeError("quota"))
    assert env.pushed == [] and any("Next steps" in e and "quota" in e for e in result.errors)
    assert result.rows_synced == 1                           # the normal sync is unaffected


def test_a_failed_log_write_is_reported_but_nothing_else_fails(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing")
    result = env.run(reads=[edited, edited, edited], log_error=RuntimeError("no access"))
    assert len(env.pushed) == 1 and env.snapshot()["acme.com"]["next_steps"] == "Send pricing"
    assert any("log" in e.lower() and "no access" in e for e in result.errors)


def test_a_whole_run_dry_run_pushes_saves_and_logs_nothing(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing")
    env.run(reads=[edited, edited, edited], dry_run=True)
    assert env.pushed == [] and env.logged == [] and env.written == []
    assert env.snapshot() == SNAP


def test_rows_without_a_hubspot_deal_are_never_pushed(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = cells("Send pricing")
    env.run(reads=[edited, edited, edited], hubspot={"acme.com": None})
    assert env.pushed == []


def test_duplicate_domain_rows_are_skipped(make_env):
    env = make_env("on")
    env.preload(SNAP)
    edited = {2: ("Send pricing", OLD_SERIAL), 3: ("Send pricing", OLD_SERIAL)}
    env.run(rows=((2, "acme.com"), (3, "ACME.com")), reads=[edited, edited, edited])
    assert env.pushed == []
