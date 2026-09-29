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
