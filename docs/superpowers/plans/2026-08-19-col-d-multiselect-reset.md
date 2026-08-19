# Col D Multi-Select Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reset the Owner? (col D) data validation in the Sales Pipeline 2026 sheet by deleting it and re-adding it — targeting ONLY col D, touching nothing else.

**Architecture:** A new standalone script `scripts/fix_col_d_multiselect.py` uses the same Google Sheets API pattern as `setup_sheet.py` but operates exclusively on col D. It deletes the existing validation, then re-adds it. The existing `setup_sheet.py` is NOT modified; no conditional formatting rules, no other column validations are touched.

**Tech Stack:** Python, gspread 6.1.4, google-auth, Google Sheets API v4 `batchUpdate`, `.env`

## Global Constraints

- Only col D (0-indexed column 3) is modified — no other columns, no CF rules, no other tabs
- `_DATA_START_ROW = 1` (0-based, skips header row 0) through `_DATA_END_ROW = 600` — matches existing scripts
- Same 8 owner names as `OWNER_OPTIONS` in `setup_sheet.py`
- Do not touch: col C, col T, col U validations, conditional formatting rules, or any other worksheet
- Run from project root with the same `.env` as the rest of the project

---

### Task 1: Write and run `scripts/fix_col_d_multiselect.py`

**Files:**
- Create: `scripts/fix_col_d_multiselect.py`

**Interfaces:**
- Consumes: `.env` keys `PIPELINE_SHEET_ID`, `PIPELINE_TAB_NAME`, `GOOGLE_CREDENTIALS_FILE`
- Produces: modifies only col D data validation in the live sheet; prints status to stdout

- [ ] **Step 1: Create the script**

Create `scripts/fix_col_d_multiselect.py` with this exact content:

```python
"""Reset col D (Owner?) data validation on the Sales Pipeline sheet.

Deletes the existing col D validation and re-adds it with the same 8 owner
names. ONLY col D is touched — no other columns, no conditional formatting.

Run:
    python scripts/fix_col_d_multiselect.py
"""

import os
import sys

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

OWNER_OPTIONS = [
    "Chandra", "Abhishek", "Piyush", "Deeksha",
    "Ayush", "Suman", "Akanksha", "Harsha",
]

_COL_D_0 = 3         # 0-based column index for col D
_DATA_START_ROW = 1  # 0-based; row 2 in Sheets (skips header)
_DATA_END_ROW = 600  # 0-based exclusive


def _col_d_range(sheet_id: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": _DATA_START_ROW,
        "endRowIndex": _DATA_END_ROW,
        "startColumnIndex": _COL_D_0,
        "endColumnIndex": _COL_D_0 + 1,
    }


def main() -> None:
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()

    if not sheet_id:
        sys.exit("ERROR: PIPELINE_SHEET_ID not set")
    if not creds_file:
        sys.exit("ERROR: GOOGLE_CREDENTIALS_FILE not set")

    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(tab_name)
    ws_id = worksheet._properties["sheetId"]
    print(f"  Sheet: {spreadsheet.title!r} → tab {tab_name!r} (sheetId={ws_id})")

    # Step 1: Delete existing col D validation (rule: None = clear)
    print("\nDeleting existing col D validation...")
    spreadsheet.batch_update({"requests": [{
        "setDataValidation": {
            "range": _col_d_range(ws_id),
            "rule": None,
        }
    }]})
    print("  Deleted.")

    # Step 2: Re-add col D validation with same 8 owner names
    print("Re-adding col D validation...")
    spreadsheet.batch_update({"requests": [{
        "setDataValidation": {
            "range": _col_d_range(ws_id),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": o} for o in OWNER_OPTIONS],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }]})
    print(f"  Re-added: {OWNER_OPTIONS}")

    print("\nDone.")
    print("Next: open the sheet → click a col D cell → Data → Data validation")
    print("      Check if 'Allow multiple selections' checkbox is now ticked.")
    print("      If still unchecked, tick it manually — the Sheets API cannot set it directly.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

```bash
cd ~/hubspot-sheet-sync && python scripts/fix_col_d_multiselect.py
```

Expected output:
```
Connecting to Google Sheets...
  Sheet: 'Sales Pipeline - 2026 - HubSpot Synced' → tab 'Sales Pipeline 2026' (sheetId=...)

Deleting existing col D validation...
  Deleted.
Re-adding col D validation...
  Re-added: ['Chandra', 'Abhishek', 'Piyush', 'Deeksha', 'Ayush', 'Suman', 'Akanksha', 'Harsha']

Done.
Next: open the sheet → click a col D cell → Data → Data validation
      Check if 'Allow multiple selections' checkbox is now ticked.
      If still unchecked, tick it manually — the Sheets API cannot set it directly.
```

- [ ] **Step 3: Check the result in the sheet UI**

1. Open the Google Sheet in a browser
2. Click any cell in col D (e.g. D2)
3. Go to **Data → Data validation** in the menu bar
4. Check the **"Allow multiple selections"** checkbox state

**If it's now enabled:** done — the reset worked.

**If it's still unchecked:** this is the known API limitation. The Sheets API v4 `ONE_OF_LIST` validation does not expose a field for "Allow multiple selections" — Google manages it internally. In that case, manually tick the checkbox in the Data validation panel for the col D range, then click **Done**. This is a one-time action per `setup_sheet.py` run.

- [ ] **Step 4: Spot-check col D values are unchanged**

Scroll through a few rows and confirm existing values like `"Chandra,Piyush"` and `"Harsha,Deeksha"` are still intact. The script resets the validation rule only — cell values are never touched.

---

## Known Limitation

The Google Sheets API v4 `DataValidationRule` for `ONE_OF_LIST` does not include a field for "Allow multiple selections". It is managed by Google's UI layer. This means every `setup_sheet.py` run will reset the checkbox to unchecked. After each `setup_sheet.py` run you'll need to either re-run this script and manually tick it, or accept single-select behaviour in the picker (data sync writes continue to work regardless).
