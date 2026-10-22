"""scripts/snapshot_values.py: letter-independent before/after check of every other tab's values."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "snapshot_values.py"


@pytest.fixture(scope="module")
def snap():
    spec = importlib.util.spec_from_file_location("snapshot_values_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_identical_snapshots_have_no_differences(snap):
    a = {"By ICP": [["a", "1"], ["b", "2"]], "Weekly": [["x"]]}
    assert snap.diff(a, {k: [r[:] for r in v] for k, v in a.items()}) == []


def test_trailing_blank_cells_and_rows_are_ignored(snap):
    a = {"T": [["a", "1", "", ""], ["", ""]]}
    b = {"T": [["a", "1"]]}
    assert snap.diff(a, b) == []


def test_a_changed_cell_is_reported_with_tab_row_and_column(snap):
    a = {"By ICP": [["a", "1"], ["b", "2"]]}
    b = {"By ICP": [["a", "1"], ["b", "3"]]}
    problems = snap.diff(a, b)
    assert len(problems) == 1 and "By ICP" in problems[0] and "B2" in problems[0] and "'2'" in problems[0]


def test_missing_and_new_tabs_are_reported(snap):
    problems = snap.diff({"A": [["x"]], "B": [["y"]]}, {"A": [["x"]], "C": [["z"]]})
    assert any("missing" in p and "B" in p for p in problems) and any("new tab" in p and "C" in p for p in problems)


def test_diff_stops_at_the_limit(snap):
    a = {"T": [[str(i)] for i in range(100)]}
    b = {"T": [[str(i + 1)] for i in range(100)]}
    assert len(snap.diff(a, b, limit=5)) == 6          # 5 problems + the "stopped" line
