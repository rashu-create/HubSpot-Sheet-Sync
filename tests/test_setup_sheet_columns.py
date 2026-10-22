"""scripts/setup_sheet.py must address the Stage / Trial Stage columns where COLUMN_MAP puts them."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_sheet.py"


@pytest.fixture(scope="module")
def setup_sheet():
    spec = importlib.util.spec_from_file_location("setup_sheet_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dropdown_columns_follow_the_column_map(setup_sheet):
    # 0-based API indexes: C=2, D=3, Stage U=20, Trial Stage V=21 (they were T=19 / U=20 before the Source move)
    assert (setup_sheet._COL_C_0, setup_sheet._COL_D_0) == (2, 3)
    assert (setup_sheet._COL_STAGE_0, setup_sheet._COL_TRIAL_STAGE_0) == (20, 21)


def test_closed_won_row_highlight_reads_the_stage_column(setup_sheet):
    rule = setup_sheet._closed_won_format_request(7)["addConditionalFormatRule"]["rule"]
    formula = rule["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
    assert formula == '=$U2="Closed Won"'
