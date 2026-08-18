"""One-shot script to configure dropdowns and conditional formatting on the pipeline sheet.

Run once after deploying changes that add new columns or change dropdown values:

    python scripts/setup_sheet.py

What it does:
  1. Fetches Sales Pipeline stage labels from HubSpot (col T dropdown)
  2. Sets col U (Trial Stage) dropdown from hardcoded HubSpot values
  3. Applies green row highlight rule when col T = "Closed Won"
"""

import os
import sys

import httpx
import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

_BASE = "https://api.hubapi.com"

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Opportunity? values (col C)
OPPORTUNITY_OPTIONS = ["Yes", "No", "Maybe"]

# Owner? values (col D)
OWNER_OPTIONS = [
    "Chandra", "Abhishek", "Piyush", "Deeksha",
    "Ayush", "Suman", "Akanksha", "Harsha",
]

# Trial Stage values from HubSpot (col U)
TRIAL_STAGE_OPTIONS = [
    "To Start",
    "Trial - Integrations",
    "Trial - Ongoing [Hot]",
    "Trial - Ongoing [Cold]",
    "Trial - Ended [Hot]",
    "Trial - Ended [Cold]",
    "Data Trial",
    "Trial Not Sure",
]

# Green: #93C77A (same shade Google Sheets uses for light green)
_GREEN = {"red": 0.576, "green": 0.780, "blue": 0.478, "alpha": 1.0}

# Col C = index 3 (1-based) → 2 (0-based for API)
_COL_C_0 = 2
# Col D = index 4 (1-based) → 3 (0-based for API)
_COL_D_0 = 3
# Col T = index 20 (1-based) → 19 (0-based for API)
_COL_T_0 = 19
# Col U = index 21 (1-based) → 20 (0-based for API)
_COL_U_0 = 20

_DATA_START_ROW = 1   # 0-based: row 2 in Sheets (skip header row 1 = index 0)
_DATA_END_ROW   = 600  # 0-based exclusive


def _fetch_stage_labels(token: str) -> list[str]:
    """Return ordered list of Sales Pipeline stage labels from HubSpot."""
    resp = httpx.get(
        f"{_BASE}/crm/v3/pipelines/deals",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()

    for pipeline in resp.json().get("results", []):
        if pipeline.get("label") == "Sales Pipeline":
            pipeline_id = pipeline["id"]
            break
    else:
        sys.exit("ERROR: 'Sales Pipeline' not found in HubSpot pipelines")

    resp = httpx.get(
        f"{_BASE}/crm/v3/pipelines/deals/{pipeline_id}/stages",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()

    stages = resp.json().get("results", [])
    stages.sort(key=lambda s: s.get("displayOrder", 0))
    labels = [s["label"] for s in stages]
    print(f"  HubSpot stage labels ({len(labels)}): {labels}")
    return labels


def _dropdown_request(sheet_id: int, col_0: int, options: list[str]) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": _DATA_START_ROW,
                "endRowIndex": _DATA_END_ROW,
                "startColumnIndex": col_0,
                "endColumnIndex": col_0 + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": o} for o in options],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }


def _closed_won_format_request(sheet_id: int) -> dict:
    # Formula applies to entire row; $T is absolute column, row number adjusts per row.
    # Row 2 = first data row (1-based), which is _DATA_START_ROW+1 in 1-based terms.
    formula = f'=$T{_DATA_START_ROW + 1}="Closed Won"'
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": _DATA_START_ROW,
                        "endRowIndex": _DATA_END_ROW,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": _GREEN},
                },
            },
            "index": 0,
        }
    }


def main() -> None:
    token = os.getenv("HUBSPOT_API_TOKEN", "").strip()
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()

    if not token:
        sys.exit("ERROR: HUBSPOT_API_TOKEN not set")
    if not sheet_id:
        sys.exit("ERROR: PIPELINE_SHEET_ID not set")
    if not creds_file:
        sys.exit("ERROR: GOOGLE_CREDENTIALS_FILE not set")

    print("Fetching HubSpot stage labels...")
    stage_labels = _fetch_stage_labels(token)

    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(tab_name)
    ws_id = worksheet._properties["sheetId"]
    print(f"  Sheet: {spreadsheet.title!r} → tab {tab_name!r} (sheetId={ws_id})")

    # Clear existing conditional format rules first (avoid accumulating duplicates on re-runs)
    print("Clearing existing conditional format rules...")
    existing = spreadsheet.fetch_sheet_metadata()
    sheets_meta = existing.get("sheets", [])
    rule_count = 0
    for s in sheets_meta:
        if s["properties"]["sheetId"] == ws_id:
            rule_count = len(s.get("conditionalFormats", []))
            break

    delete_requests = [
        {"deleteConditionalFormatRule": {"sheetId": ws_id, "index": 0}}
        for _ in range(rule_count)
    ]
    if delete_requests:
        spreadsheet.batch_update({"requests": delete_requests})
        print(f"  Removed {rule_count} existing rule(s)")

    # Build and apply new requests
    requests = [
        _dropdown_request(ws_id, _COL_C_0, OPPORTUNITY_OPTIONS),
        _dropdown_request(ws_id, _COL_D_0, OWNER_OPTIONS),
        _dropdown_request(ws_id, _COL_T_0, stage_labels),
        _dropdown_request(ws_id, _COL_U_0, TRIAL_STAGE_OPTIONS),
        _closed_won_format_request(ws_id),
    ]

    print("Applying dropdowns and conditional formatting...")
    spreadsheet.batch_update({"requests": requests})

    print("Done.")
    print(f"  Col C (Opportunity?) dropdown: {len(OPPORTUNITY_OPTIONS)} options")
    print(f"  Col D (Owner?) dropdown: {len(OWNER_OPTIONS)} options")
    print(f"  Col T (Stage) dropdown: {len(stage_labels)} options")
    print(f"  Col U (Trial Stage) dropdown: {len(TRIAL_STAGE_OPTIONS)} options")
    print("  Green highlight rule added for Closed Won rows")


if __name__ == "__main__":
    main()
