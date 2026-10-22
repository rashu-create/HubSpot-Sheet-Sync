"""Sheet-side guardrails for the Next Steps / Due Date write-back (requests only; the script applies them)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_next_steps_guardrails.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("guardrails_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_due_date_column_gets_a_strict_valid_date_rule(guard):
    reqs = guard.build_requests(sheet_id=7)
    rule_req = next(r for r in reqs if "setDataValidation" in r)["setDataValidation"]
    rng = rule_req["range"]
    assert rng["sheetId"] == 7 and rng["startColumnIndex"] == 19 and rng["endColumnIndex"] == 20   # col T
    assert rng["startRowIndex"] == 1                                                              # header untouched
    assert rule_req["rule"]["condition"] == {"type": "DATE_IS_VALID"}
    assert rule_req["rule"]["strict"] is True


def test_both_header_cells_get_a_note(guard):
    notes = [r["updateCells"] for r in guard.build_requests(7) if "updateCells" in r]
    assert sorted(n["start"]["columnIndex"] for n in notes) == [18, 19]          # S and T
    for n in notes:
        assert n["start"]["rowIndex"] == 0 and n["fields"] == "note"
        assert "HubSpot" in n["rows"][0]["values"][0]["note"] and "10 AM" in n["rows"][0]["values"][0]["note"]


def test_nothing_touches_conditional_formatting_or_other_columns(guard):
    kinds = {k for r in guard.build_requests(7) for k in r}
    assert kinds == {"setDataValidation", "updateCells"}
