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
