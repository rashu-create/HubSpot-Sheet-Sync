# Qualified Column (AI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Qualified" column at AI in the Sales Pipeline 2026 sheet — "Yes" if size of sales team > 0 OR funding > $5M, "No" otherwise — without disrupting any downstream consumers (ae-kpi-tracker, pricing-alerts).

**Architecture:** Two services touch this sheet. `hubspot-sheet-sync` writes data columns A–AO (post-change); `ae-kpi-tracker` reads from it and writes DEDUP flag at AO (pre-change → AP post-change). Inserting a column at AI shifts everything from AI rightward by one: AI→AJ (Closure Month), AJ→AK (Opp loss reason), AK→AL (Opp loss reason deepdive), AN→AO (Deal Amount), AO→AP (DEDUP_COL). Code in both services must be updated before the sheet column is inserted, then the one-time insertion script runs, then both services are deployed and ae-kpi-tracker /api/setup is called to rewrite the DEDUP ARRAYFORMULA to the new column AP.

**Tech Stack:** Python, gspread, Google Sheets API, pytest

## Global Constraints

- All tests in `~/hubspot-sheet-sync/` must pass: `cd ~/hubspot-sheet-sync && pytest`
- All tests in `~/ae-kpi-tracker/` must pass: `cd ~/ae-kpi-tracker && pytest`
- Never write to column A of the pipeline sheet (domain — read-only input)
- Never hardcode credentials — use `.env` / env vars
- `hubspot-sheet-sync` COLUMN_MAP drives which columns are written; columns not in COLUMN_MAP are left untouched by the sync
- `ae-kpi-tracker` uses `DEDUP_COL` and `AMOUNT_COL` constants from `config.py` — change the constants, not hardcoded strings
- The column insertion script must be idempotent-safe: it inserts exactly one column, then stops — never re-run it without verifying the sheet first

---

## Column layout before and after

| Before | After | Content |
|--------|-------|---------|
| AI | AJ | Closure Month (deal closedate) |
| AJ | AK | Opportunity loss reason |
| AK | AL | Opportunity loss reason deepdive |
| AL | AM | SKIP — Trial loss reason (manual) |
| AM | AN | SKIP — Intent signals (manual) |
| AN | AO | Deal Amount (AMOUNT_COL in ae-kpi-tracker) |
| AO | AP | DEDUP_COL (ae-kpi-tracker ARRAYFORMULA) |
| *(new)* AI | AI | **Qualified** ("Yes"/"No", computed by hubspot-sheet-sync) |

---

## Files changed

| Repo | File | Action |
|------|------|--------|
| hubspot-sheet-sync | `src/hubspot.py` | Add `_compute_qualified()` helper + wire into `get_row_data()` |
| hubspot-sheet-sync | `src/mapping.py` | Insert AI entry; shift AI→AJ, AJ→AK, AK→AL; update SKIP comments; shift AN→AO |
| hubspot-sheet-sync | `tests/test_sync.py` | Add `TestComputeQualified` class (7 tests) |
| hubspot-sheet-sync | `tests/test_mapping.py` | Rename AN test → AO; add AI Qualified test |
| hubspot-sheet-sync | `scripts/insert_qualified_column.py` | **NEW** — one-time sheet column insertion script |
| ae-kpi-tracker | `src/config.py` | `AMOUNT_COL "AN"→"AO"`, `DEDUP_COL "AO"→"AP"` |
| ae-kpi-tracker | `src/pipeline_tabs.py` | Update docstring on `write_dedup_column()` (line ~283) |

---

## Task 1: Add `_compute_qualified()` to hubspot-sheet-sync + tests

**Files:**
- Modify: `~/hubspot-sheet-sync/src/hubspot.py` (after `_compute_icp_size`, ~line 742)
- Modify: `~/hubspot-sheet-sync/src/hubspot.py` (inside `get_row_data`, after `computed["still_active"]`, ~line 888)
- Test: `~/hubspot-sheet-sync/tests/test_sync.py`

**Interfaces:**
- Consumes: `_parse_int()` (already in hubspot.py at line 668) — reuse for sales_size
- Produces: `_compute_qualified(company_props: dict) -> str` — returns "Yes" or "No"
- `get_row_data()` stores result as `computed["qualified"]`; Task 2 wires it to column AI via COLUMN_MAP

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/test_sync.py` (find the existing `TestComputeIcpSize` class and add the new class right after it):

```python
class TestComputeQualified:
    """_compute_qualified returns 'Yes' if sales_team > 0 OR funding > $5M."""

    def test_yes_when_sales_team_nonzero(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "5", "total_funding": "0"}) == "Yes"

    def test_yes_when_funding_above_5m(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "6000000"}) == "Yes"

    def test_no_when_both_zero(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "0"}) == "No"

    def test_no_when_both_blank(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({}) == "No"

    def test_yes_when_both_met(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": "10", "total_funding": "10000000"}) == "Yes"

    def test_no_when_funding_exactly_5m(self):
        from src.hubspot import _compute_qualified
        # Strictly greater than $5M required — exactly 5M is "No"
        assert _compute_qualified({"r__size_of_sales_team": "0", "total_funding": "5000000"}) == "No"

    def test_no_when_props_are_none(self):
        from src.hubspot import _compute_qualified
        assert _compute_qualified({"r__size_of_sales_team": None, "total_funding": None}) == "No"
```

- [ ] **Step 2: Run tests — expect 7 failures**

```bash
cd ~/hubspot-sheet-sync && pytest tests/test_sync.py -k "TestComputeQualified" -v
```

Expected: 7 FAILED with `ImportError: cannot import name '_compute_qualified'`

- [ ] **Step 3: Add `_compute_qualified` to hubspot.py**

Insert this function after `_compute_icp_size` (around line 742), before the `# ── Main entry point` comment:

```python
def _compute_qualified(company_props: dict) -> str:
    """Return 'Yes' if sales team size > 0 or total funding > $5M, else 'No'."""
    sales_size = _parse_int(company_props.get("r__size_of_sales_team"))
    funding_raw = company_props.get("total_funding") or ""
    try:
        funding = float(str(funding_raw).replace(",", "").strip()) if funding_raw else 0.0
    except (ValueError, TypeError):
        funding = 0.0
    return "Yes" if (sales_size > 0 or funding > 5_000_000) else "No"
```

- [ ] **Step 4: Wire into `get_row_data()` in hubspot.py**

Find the block that sets `computed["still_active"]` (~line 882–888). Add the new line immediately after it:

```python
        # Still active? (derived from stage label)
        stage_lower = stage_label.lower()
        if any(s in stage_lower for s in ("won", "lost", "converted", "irrelevant")):
            computed["still_active"] = "No"
        elif "pushed out" in stage_lower:
            computed["still_active"] = "Pushed Out"
        else:
            computed["still_active"] = "Yes"

        # Qualified: sales team size > 0 OR funding > $5M
        computed["qualified"] = _compute_qualified(company_props)
```

- [ ] **Step 5: Run tests — expect all 7 to pass**

```bash
cd ~/hubspot-sheet-sync && pytest tests/test_sync.py -k "TestComputeQualified" -v
```

Expected: 7 PASSED

- [ ] **Step 6: Run full test suite — must stay green**

```bash
cd ~/hubspot-sheet-sync && pytest -v
```

Expected: all existing tests PASS + 7 new PASS

- [ ] **Step 7: Commit**

```bash
cd ~/hubspot-sheet-sync
git add src/hubspot.py tests/test_sync.py
git commit -m "feat: add _compute_qualified helper (sales_team>0 or funding>5M)"
```

---

## Task 2: Update COLUMN_MAP in mapping.py + fix tests

**Files:**
- Modify: `~/hubspot-sheet-sync/src/mapping.py` (COLUMN_MAP, ~lines 91–98)
- Modify: `~/hubspot-sheet-sync/tests/test_mapping.py` (rename AN test → AO; add AI test)

**Interfaces:**
- Consumes: `computed["qualified"]` from Task 1 — mapped via `prop_key="qualified"`, `source="computed"`
- Produces: updated COLUMN_MAP where AI="Qualified", AJ="Closure Month", AK="Opp loss reason", AL="Opp loss reason deepdive", AO="Deal Amount"

- [ ] **Step 1: Update COLUMN_MAP in mapping.py**

Find lines 91–98 in `src/mapping.py` (the block starting with `# col AH = SKIP (manual)`) and replace them:

```python
    # col AH = SKIP (manual)
    ("AI", "Qualified",                          "computed", "qualified",                         "passthrough"),
    ("AJ", "Closure Month",                      "deal",     "closedate",                         "month_year"),
    ("AK", "Opportunity loss reason",            "deal",     "closed_lost_reasons",               "passthrough"),
    ("AL", "Opportunity loss reason - deepdive", "deal",     "closed_lost_details",               "passthrough"),
    # col AM = SKIP (Trial loss reason — manual)
    # col AN = SKIP (Intent signals — manual)
    ("AO", "Deal Amount",                        "deal",     "amount",                            "number"),
```

- [ ] **Step 2: Update test_mapping.py — rename AN test to AO**

Find `test_column_an_in_column_map` in `tests/test_mapping.py` and replace it:

```python
def test_column_ao_in_column_map():
    """Column AO must be mapped to deal amount (shifted from AN after Qualified inserted at AI)."""
    from src.mapping import COLUMN_MAP
    cols = {entry[0]: entry for entry in COLUMN_MAP}
    assert "AO" in cols, "AO column entry missing from COLUMN_MAP"
    col, header, source, prop, formatter = cols["AO"]
    assert source == "deal"
    assert prop == "amount"
    assert formatter == "number"
```

- [ ] **Step 3: Add AI Qualified test to test_mapping.py**

Add this function right after `test_column_ao_in_column_map`:

```python
def test_column_ai_in_column_map():
    """Column AI must be the Qualified computed column."""
    from src.mapping import COLUMN_MAP
    cols = {entry[0]: entry for entry in COLUMN_MAP}
    assert "AI" in cols, "AI Qualified column missing from COLUMN_MAP"
    col, header, source, prop, formatter = cols["AI"]
    assert header == "Qualified"
    assert source == "computed"
    assert prop == "qualified"
    assert formatter == "passthrough"


def test_column_aj_is_closure_month():
    """Closure Month must shift from AI to AJ after inserting Qualified at AI."""
    from src.mapping import COLUMN_MAP
    cols = {entry[0]: entry for entry in COLUMN_MAP}
    assert "AJ" in cols, "AJ Closure Month missing from COLUMN_MAP"
    col, header, source, prop, formatter = cols["AJ"]
    assert prop == "closedate"
    assert formatter == "month_year"
```

- [ ] **Step 4: Run mapping tests**

```bash
cd ~/hubspot-sheet-sync && pytest tests/test_mapping.py -v
```

Expected: all PASS (old `test_column_an_in_column_map` is gone; `test_column_ao_in_column_map`, `test_column_ai_in_column_map`, `test_column_aj_is_closure_month` pass)

- [ ] **Step 5: Run full test suite**

```bash
cd ~/hubspot-sheet-sync && pytest -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
cd ~/hubspot-sheet-sync
git add src/mapping.py tests/test_mapping.py
git commit -m "feat: insert Qualified at col AI; shift AI-AN downstream one column"
```

---

## Task 3: Update ae-kpi-tracker column constants

**Files:**
- Modify: `~/ae-kpi-tracker/src/config.py` (AMOUNT_COL, DEDUP_COL)
- Modify: `~/ae-kpi-tracker/src/pipeline_tabs.py` (docstring only, ~line 283)

**Interfaces:**
- Consumes: knowledge that Deal Amount moves from AN→AO and DEDUP_COL moves from AO→AP
- Produces: updated constants that all formula_helpers and pipeline_tabs code picks up automatically (they already reference the constants, not hardcoded letters)

- [ ] **Step 1: Update config.py**

In `~/ae-kpi-tracker/src/config.py`, change lines 14–15 from:

```python
AMOUNT_COL = "AN"  # column in Sales Pipeline 2026 holding HubSpot deal amount
DEDUP_COL = "AO"  # first-occurrence flag; written by setup to 'Sales Pipeline 2026' col AO
```

to:

```python
AMOUNT_COL = "AO"  # column in Sales Pipeline 2026 holding HubSpot deal amount
DEDUP_COL = "AP"  # first-occurrence flag; written by setup to 'Sales Pipeline 2026' col AP
```

- [ ] **Step 2: Update write_dedup_column docstring in pipeline_tabs.py**

Find the `write_dedup_column` function (~line 282) and update its docstring. Change:

```python
def write_dedup_column(spreadsheet: gspread.Spreadsheet) -> None:
    """Write a first-occurrence dedup flag to col AO of the source tab.

    AO1 = header "Is First?"
    AO2 = ARRAYFORMULA that outputs 1 for the first row with a given domain
          (col A value) and 0 for every subsequent duplicate row.
```

to:

```python
def write_dedup_column(spreadsheet: gspread.Spreadsheet) -> None:
    """Write a first-occurrence dedup flag to col AP of the source tab.

    AP1 = header "Is First?"
    AP2 = ARRAYFORMULA that outputs 1 for the first row with a given domain
          (col A value) and 0 for every subsequent duplicate row.
```

- [ ] **Step 3: Run ae-kpi-tracker tests**

```bash
cd ~/ae-kpi-tracker && pytest -v
```

Expected: all 108 tests PASS. The formula_helpers and pipeline_tabs tests already reference `DEDUP_COL` and `AMOUNT_COL` via `from src.config import ...`, so they pick up the new values automatically.

- [ ] **Step 4: Commit**

```bash
cd ~/ae-kpi-tracker
git add src/config.py src/pipeline_tabs.py
git commit -m "fix: shift AMOUNT_COL AN→AO and DEDUP_COL AO→AP for new Qualified column at AI"
```

---

## Task 4: Write the one-time column insertion script

**Files:**
- Create: `~/hubspot-sheet-sync/scripts/insert_qualified_column.py`

**Interfaces:**
- Consumes: `GOOGLE_CREDENTIALS_FILE`, `PIPELINE_SHEET_ID`, `PIPELINE_TAB_NAME` from `.env`
- Produces: sheet has a blank column inserted at AI with header "Qualified" in AI1

**⚠️ This script must only be run ONCE.** Running it a second time inserts another blank column at AI. Verify the sheet before re-running.

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""One-time: insert 'Qualified' column at AI in Sales Pipeline 2026 tab.

Run ONCE from ~/hubspot-sheet-sync/:
    python3 scripts/insert_qualified_column.py

What it does:
  1. Inserts a blank column at position AI (col index 35, 0-based index 34)
     in the Sales Pipeline 2026 tab. All existing columns from AI rightward
     shift one position: Closure Month AI→AJ, Opp loss reason AJ→AK, etc.
     Google Sheets also auto-shifts any formulas (e.g., DEDUP ARRAYFORMULA
     that was at AO shifts to AP).
  2. Writes "Qualified" header to AI1.

After running this script:
  - Deploy hubspot-sheet-sync (Qualified at AI will be written on next sync)
  - Deploy ae-kpi-tracker (DEDUP_COL now AP, AMOUNT_COL now AO)
  - Call POST /api/setup on ae-kpi-tracker to rewrite DEDUP ARRAYFORMULA to AP
  - Trigger a manual sync on hubspot-sheet-sync or wait for 08:00 UTC daily run
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import gspread
from google.oauth2.service_account import Credentials

from src.mapping import col_letter_to_index

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_TARGET_COL_LETTER = "AI"


def main() -> None:
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()

    if not creds_file:
        raise RuntimeError("GOOGLE_CREDENTIALS_FILE is not set in .env")
    if not sheet_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set in .env")

    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    ws = spreadsheet.worksheet(tab_name)

    ai_col_idx = col_letter_to_index(_TARGET_COL_LETTER)  # 35
    start_index = ai_col_idx - 1  # 0-based → 34

    print(f"Inserting blank column at {_TARGET_COL_LETTER} (0-based index {start_index}) in '{tab_name}'...")
    spreadsheet.batch_update({
        "requests": [{
            "insertDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_index,
                    "endIndex": start_index + 1,
                },
                "inheritFromBefore": False,
            }
        }]
    })
    print(f"Column inserted. Existing columns from AI rightward shifted by one.")

    ws.update_acell(f"{_TARGET_COL_LETTER}1", "Qualified")
    print(f"Header 'Qualified' written to {_TARGET_COL_LETTER}1.")
    print("Done. See script docstring for next steps.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script is syntactically valid**

```bash
cd ~/hubspot-sheet-sync && python3 -c "import ast; ast.parse(open('scripts/insert_qualified_column.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit the script (do NOT run it yet — run it in Task 5)**

```bash
cd ~/hubspot-sheet-sync
git add scripts/insert_qualified_column.py
git commit -m "chore: add one-time script to insert Qualified column at AI"
```

---

## Task 5: Execute the migration — insert column, deploy, verify

**⚠️ Time-sensitive:** Run all steps below BEFORE 08:00 UTC (1:30 PM IST) to avoid a sync writing to the wrong columns. Check current time before starting.

**Prerequisites before starting:**
- Tasks 1–4 are all committed and pushed to GitHub
- Both services on VM are currently at their old code (no early deploy)
- You have SSH access to `sales-production` VM

- [ ] **Step 1: Confirm all local tests pass on both repos**

```bash
cd ~/hubspot-sheet-sync && pytest -v && echo "hubspot-sheet-sync OK"
cd ~/ae-kpi-tracker && pytest -v && echo "ae-kpi-tracker OK"
```

Expected: both print "OK"

- [ ] **Step 2: Run the column insertion script**

```bash
cd ~/hubspot-sheet-sync && python3 scripts/insert_qualified_column.py
```

Expected output:
```
Inserting blank column at AI (0-based index 34) in 'Sales Pipeline 2026'...
Column inserted. Existing columns from AI rightward shifted by one.
Header 'Qualified' written to AI1.
Done. See script docstring for next steps.
```

- [ ] **Step 3: Verify the sheet in Google Sheets UI**

Open the sheet and check:
- AI1 shows "Qualified" (blank data below — no values yet)
- AJ1 shows "Closure Month"
- AO1 shows "Deal Amount"
- AP1 shows "Is First?" (DEDUP ARRAYFORMULA auto-shifted from AO)
- AP2 should contain `=ARRAYFORMULA(IF(A2:A="",0,...))` (auto-shifted by Google Sheets)

If anything looks wrong, **stop here** and diagnose before proceeding.

- [ ] **Step 4: Deploy hubspot-sheet-sync to VM**

```bash
cd ~/hubspot-sheet-sync && bash deploy/deploy.sh
```

- [ ] **Step 5: Deploy ae-kpi-tracker to VM**

```bash
cd ~/ae-kpi-tracker && bash deploy/deploy.sh
```

(SSH command, systemd, Consul → see vm-ops.md memory for exact deploy flow)

- [ ] **Step 6: Run ae-kpi-tracker /api/setup to rewrite DEDUP to AP**

```bash
curl -X POST https://ae-kpi-tracker.data.reo.dev/api/setup
```

Expected: `{"status": "ok"}` or similar. This clears and rewrites the DEDUP ARRAYFORMULA at AP.

- [ ] **Step 7: Trigger a manual sync on hubspot-sheet-sync**

```bash
curl -X POST https://hubspot-sheet-sync.data.reo.dev/api/sync
```

Wait 3–5 minutes for the sync to complete (~344 rows).

- [ ] **Step 8: Verify results in the sheet**

Open the sheet and confirm:
- Column AI (Qualified): "Yes" or "No" for each row — spot-check:
  - A row with J > 0 (e.g., sales team = 5) → "Yes"
  - A row with H > 5000000 (e.g., funding = $10M) → "Yes"
  - A row with J = 0 and H = 0 → "No"
- Column AJ (Closure Month): values like "Aug 2026" (was AI before)
- Column AO (Deal Amount): dollar amounts (was AN before)
- Column AP (Is First?): 1s and 0s from ARRAYFORMULA (was AO before)
- ae-kpi-tracker Dashboard/Summary tabs still show correct KPI numbers

- [ ] **Step 9: Verify ae-kpi-tracker KPIs are unchanged**

Open `ae-kpi-tracker.data.reo.dev` and spot-check:
- Total Meetings, Opportunities, Conversions still match known values
- Weighted Deal Value and Absolute Deal Value still show correct numbers (they read AO now)
- No 0s or blanks where numbers used to be

- [ ] **Step 10: Update CLAUDE.md in ae-kpi-tracker**

In `~/ae-kpi-tracker/CLAUDE.md`, find the "What NOT to change without checking" section and update:

```
- `DEDUP_COL = "AP"` in `config.py` — col AP of `Sales Pipeline 2026` holds the
  first-occurrence flag. Do not change this letter without also updating the ARRAYFORMULA
  written by `write_dedup_column()` and verifying no other service writes to AP.
  (`hubspot-sheet-sync` ends at col AO — safe.)
```

Also update the `AMOUNT_COL` reference in the "Column reference" section:
```
- Both S and T reference col AO (Deal Amount) from `Sales Pipeline 2026`.
```

- [ ] **Step 11: Update hubspot-sheet-sync memory file**

Update `~/.claude/projects/-Users-rashuraj/memory/hubspot-sheet-sync.md`:
- Column map section: add `("AI", "Qualified")`, update AJ–AO and SKIP comments
- Open items: remove "No-owner deals ~20" if resolved; no new open items

- [ ] **Step 12: Final commit for documentation**

```bash
cd ~/ae-kpi-tracker
git add CLAUDE.md
git commit -m "docs: update column references after Qualified column inserted at AI"
```
