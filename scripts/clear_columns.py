"""One-shot script to clear manual columns that have stale or undefined data.

Clears:
  - Col AF  (Real Opportunity?)        — logic not yet defined, leave blank
  - Col AH  (Source of meeting [Refined]) — logic not yet defined, leave blank

Rows 2 onward (header row 1 is untouched).

Run from the project root:

    python scripts/clear_columns.py
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

# Columns to clear (A=1, Z=26, AA=27, AF=32, AH=34)
_COLS_TO_CLEAR = [
    ("AF", 32, "Real Opportunity?"),
    ("AH", 34, "Source of meeting [Refined]"),
]

_HEADER_ROW = 1   # 1-based; keep this row intact
_DATA_START  = 2  # 1-based; first data row


def _col_letter(idx_1based: int) -> str:
    result = ""
    n = idx_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def main() -> None:
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    sheet_id   = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name   = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()

    if not creds_file or not sheet_id:
        sys.exit("ERROR: GOOGLE_CREDENTIALS_FILE and PIPELINE_SHEET_ID must be set in .env")

    creds  = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    client = gspread.authorize(creds)

    ss = client.open_by_key(sheet_id)
    ws = ss.worksheet(tab_name)

    # Find last row with any data so we don't write beyond the sheet
    all_values = ws.get_all_values()
    last_data_row = len(all_values)  # 1-based; includes header
    if last_data_row < _DATA_START:
        print("Sheet appears empty — nothing to clear.")
        return

    n_data_rows = last_data_row - _HEADER_ROW
    print(f"Sheet: '{tab_name}' — {n_data_rows} data rows (rows {_DATA_START}–{last_data_row})")

    updates = []
    for col_letter, col_1based, label in _COLS_TO_CLEAR:
        range_a1 = f"'{tab_name}'!{col_letter}{_DATA_START}:{col_letter}{last_data_row}"
        empty_column = [[""] for _ in range(n_data_rows)]
        updates.append({
            "range": range_a1,
            "values": empty_column,
        })
        print(f"  Queuing clear: col {col_letter} ({label})  →  {range_a1}")

    ss.values_batch_update({
        "valueInputOption": "RAW",
        "data": updates,
    })

    print(f"\nDone — {len(_COLS_TO_CLEAR)} columns cleared.")
    print("These columns are marked SKIP in COLUMN_MAP so the sync will not touch them again.")


if __name__ == "__main__":
    main()
