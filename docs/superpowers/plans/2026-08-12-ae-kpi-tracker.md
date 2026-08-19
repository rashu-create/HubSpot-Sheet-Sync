# AE KPI Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New Python service (`~/ae-kpi-tracker`) that builds and maintains the "2026 Pipeline Analysis" AE KPI tracker Google Sheet — formula-based analysis tabs (Dashboard, Summary, By ICP Size, By ICP Type, per-AE, Secondary Owners) synced from the Sales Pipeline sheet, plus a Weekly tracker sync from "Sales Weekly Updates - 2026."

**Architecture:** New standalone project following hubspot-sheet-sync patterns. Two operations: (1) SETUP — one-shot tab structure build writing formula strings via gspread; (2) SYNC — scheduled refresh of RawData tab (from Sales Pipeline sheet, direct copy — avoids IMPORTRANGE auth issues) + Weekly tracker tab from Sales Weekly Updates (color-aware read/write via Sheets API v4).

**Tech Stack:** Python 3.11+, FastAPI, APScheduler, gspread 6.x, google-auth, google-api-python-client (Sheets API v4), python-dotenv, pytest

## Global Constraints

- Python 3.11+
- Same service account credentials as hubspot-sheet-sync (`credentials.json`)
- Follow hubspot-sheet-sync patterns: `RunResult` dataclass, `_get_client()` factory, env vars via dotenv, chunked batch writes
- `.env` for all secrets — `.env.example` committed, `.env` in `.gitignore`
- Port 8009
- Never hardcode sheet IDs or credentials
- Black formatting

---

## Open Questions (resolve before executing Tasks 6 & 7)

**Q1 — Target sheet ID:** What is the Google Sheet ID of the test/target AE KPI tracker sheet?

**Q2 — Weekly sheet ID:** What is the Google Sheet ID of "Sales Weekly Updates - 2026"?

**Q3 — Weekly source structure:** In "Sales Weekly Updates - 2026", does each "ws DD Mon YYYY" tab contain ALL weeks as columns (cumulative/year-long view), or only that single week's data? The Weekly tracker in the AE KPI tracker shows all weeks as columns — does the Python script need to merge multiple "ws" tabs, or copy one?

**Q4 — Color-based counts:** The Weekly tracker's count rows (OSS / OSS Affiliated / Non OSS / Enterprise / Mid Market / SMB / etc.) are currently computed via custom `COUNTBYFILL` / `COUNTBYFONT` Apps Script functions that count by cell background/font color. Should Python: (a) read cell colors from source and recount in Python (clean, no Apps Script dependency), or (b) read the pre-computed count values directly from source sheet cells?

**Q5 — Weekly - By Owner sections:** The Apps Script `WBO_SECTIONS` contains 8 sections (A-H) with hardcoded row offsets matching the "Weekly tracker" layout. Should the Python implementation use the same row offsets as the Apps Script, or derive them dynamically from the actual content?

Tasks 1–5 and 8–9 can be executed immediately. Tasks 6 and 7 require Q3/Q4/Q5 answers.

---

## File Map

| File | Responsibility |
|------|----------------|
| `src/config.py` | Env var accessors, AE names, bucket definitions, color palette |
| `src/sheets_client.py` | gspread factory, Sheets API v4 service, `batch_update()`, `get_or_create_tab()`, `write_formulas()` |
| `src/formula_helpers.py` | `col_letter()`, `cif()` (COUNTIFS builder), `metric_formulas()`, `write_table()`, `place_filter()` — ported from Apps Script |
| `src/raw_data.py` | Read Sales Pipeline sheet → write RawData tab in target (direct copy, no IMPORTRANGE) |
| `src/pipeline_tabs.py` | Build Dashboard, Summary, By ICP Size, By ICP Type, per-AE (×7), Secondary Owners tabs |
| `src/weekly_sync.py` | Read Sales Weekly Updates (color-aware) → write Weekly tracker tab |
| `src/weekly_by_owner.py` | Build Weekly - By Owner tab (formula-based, owner dropdown, conditional formatting) |
| `src/setup.py` | One-shot: call all builders in order |
| `src/sync.py` | `RunResult` dataclass + `run_sync()` orchestrator (RawData + Weekly refresh) |
| `src/api.py` | FastAPI app — dashboard + `/api/sync` + `/api/setup` + `/api/status` |
| `src/scheduler.py` | APScheduler — daily sync job |
| `tests/test_formula_helpers.py` | Unit tests for formula builders |
| `tests/test_raw_data.py` | Tests for raw data reading/writing |
| `tests/test_weekly_sync.py` | Tests for tab-discovery + color-count helpers |
| `tests/test_pipeline_tabs.py` | Tests that correct tab names are created |

---

## Task 1: Project Scaffold + Config

**Files:**
- Create: `~/ae-kpi-tracker/requirements.txt`
- Create: `~/ae-kpi-tracker/.env.example`
- Create: `~/ae-kpi-tracker/src/__init__.py`
- Create: `~/ae-kpi-tracker/src/config.py`
- Create: `~/ae-kpi-tracker/tests/__init__.py`

**Interfaces:**
- Produces: `from src.config import AES, SECONDARY_AES, SIZE_BUCKETS, TYPE_BUCKETS, PALETTE, FONT, METRICS`

- [ ] **Step 1: Create project directory**

```bash
mkdir -p ~/ae-kpi-tracker/src ~/ae-kpi-tracker/tests ~/ae-kpi-tracker/scripts
cd ~/ae-kpi-tracker && git init
```

- [ ] **Step 2: Create requirements.txt**

```
fastapi==0.115.5
uvicorn[standard]==0.32.1
apscheduler==3.10.4
gspread==6.1.4
google-auth==2.36.0
google-api-python-client==2.155.0
python-dotenv==1.0.1
slack-sdk==3.33.4
pytest==8.3.4
pytest-cov==6.0.0
```

- [ ] **Step 3: Create .env.example**

```
GOOGLE_CREDENTIALS_FILE=credentials.json
TARGET_SHEET_ID=           # AE KPI tracker test Google Sheet ID
PIPELINE_SHEET_ID=         # hubspot-sheet-sync output sheet (Sales Pipeline 2026)
PIPELINE_TAB_NAME=Sales Pipeline 2026
WEEKLY_SHEET_ID=           # Sales Weekly Updates - 2026 sheet ID
PORT=8009
LOG_LEVEL=INFO
SCHEDULER_ENABLED=false
```

- [ ] **Step 4: Create src/config.py**

```python
"""Constants and env var accessors."""
import os


def get_env(key: str, default: str = "") -> str:
    val = os.getenv(key, default).strip()
    if not val and not default:
        raise RuntimeError(f"{key} is not set")
    return val


AES = ["Chandra", "Harsha", "Akanksha", "Ayush", "Suman", "Deeksha", "Piyush"]
SECONDARY_AES = ["Chandra", "Harsha", "Akanksha", "Ayush", "Suman"]

SIZE_BUCKETS = [
    ("Enterprise [500+ Emp.]", [("I", ">=500")]),
    ("Commercial [200 - 500 Emp.]", [("I", ">=200"), ("I", "<500")]),
    ("SMB [<200 Emp & 2+ Sales]", [("I", "<200"), ("J", ">=2")]),
    ("Startup [<200 Emp & 0/1 Sales]", [("I", "<200"), ("J", "<=1")]),
]
TYPE_BUCKETS = [
    ("OSS", [("F", "OSS")]),
    ("OSS Affiliated", [("F", "OSS Affiliated")]),
    ("Closed Source", [("F", "Closed Source")]),
]

PALETTE = {
    "ink": "#6A5FA0", "head": "#453E66", "colh": "#ECE7F8", "sect": "#DCD4F2",
    "sub": "#E2DBF5", "tot": "#EFEAF9", "subtle": "#F8F5FD", "pos1": "#DCF0DE",
    "pos2": "#EBF5E8", "att": "#FCEDD6", "neu": "#F4F2FA", "input": "#FCF4D2",
    "border": "#E8E3F4", "borderEm": "#C3B4E6", "note": "#8A8A99",
}
FONT = "Calibri"

METRICS = [
    "Meetings", "Opportunities", "Trial [POC]", "Opp, no trial", "SQL",
    "In Contract", "Converted", "Inactive [Lost + Won]", "Should Convert",
    "Might Convert", "Active Pipeline", "Pushed Out",
    "Mtg→Opp %", "Opp→Conv %", "Mtg→Conv %",
]
```

- [ ] **Step 5: Create empty __init__ files and .gitignore**

```bash
touch ~/ae-kpi-tracker/src/__init__.py ~/ae-kpi-tracker/tests/__init__.py
echo ".env\ncredentials.json\n__pycache__\n*.pyc\n.pytest_cache\n.coverage" > ~/ae-kpi-tracker/.gitignore
```

- [ ] **Step 6: Install dependencies**

```bash
cd ~/ae-kpi-tracker && pip install -r requirements.txt
```

- [ ] **Step 7: Commit**

```bash
cd ~/ae-kpi-tracker && git add . && git commit -m "feat: project scaffold, config constants"
```

---

## Task 2: Sheets Client

**Files:**
- Create: `src/sheets_client.py`
- Create: `tests/test_sheets_client.py`

**Interfaces:**
- Produces: `get_gspread_client() -> gspread.Client`
- Produces: `get_sheets_service()` → Sheets API v4 Resource
- Produces: `batch_update(spreadsheet_id: str, requests: list[dict]) -> None`
- Produces: `get_or_create_tab(spreadsheet, name: str) -> gspread.Worksheet`
- Produces: `write_formulas(spreadsheet, tab_name: str, data: list[dict]) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sheets_client.py
import pytest
from unittest.mock import patch, MagicMock


def test_get_client_missing_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    from src.sheets_client import get_gspread_client
    with pytest.raises(RuntimeError, match="GOOGLE_CREDENTIALS_FILE"):
        get_gspread_client()


def test_get_or_create_tab_existing():
    import gspread
    from src.sheets_client import get_or_create_tab
    mock_ss = MagicMock()
    mock_ws = MagicMock()
    mock_ss.worksheet.return_value = mock_ws
    result = get_or_create_tab(mock_ss, "Dashboard")
    assert result is mock_ws
    mock_ss.worksheet.assert_called_once_with("Dashboard")


def test_get_or_create_tab_creates_new():
    import gspread
    from src.sheets_client import get_or_create_tab
    mock_ss = MagicMock()
    mock_ss.worksheet.side_effect = gspread.WorksheetNotFound
    mock_ws = MagicMock()
    mock_ss.add_worksheet.return_value = mock_ws
    result = get_or_create_tab(mock_ss, "NewTab")
    assert result is mock_ws
    mock_ss.add_worksheet.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/ae-kpi-tracker && pytest tests/test_sheets_client.py -v
```

Expected: `ImportError` or `ModuleNotFoundError`

- [ ] **Step 3: Implement src/sheets_client.py**

```python
"""Google Sheets client factory and Sheets API v4 helpers."""
import logging
import os

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_creds() -> Credentials:
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    if not creds_file:
        raise RuntimeError("GOOGLE_CREDENTIALS_FILE is not set")
    return Credentials.from_service_account_file(creds_file, scopes=_SCOPES)


def get_gspread_client() -> gspread.Client:
    return gspread.authorize(_get_creds())


def get_sheets_service():
    """Return Sheets API v4 Resource — needed for formatting + color read/write."""
    return build("sheets", "v4", credentials=_get_creds(), cache_discovery=False)


def batch_update(spreadsheet_id: str, requests: list[dict]) -> None:
    """Send a batchUpdate request (formatting, validation, conditional format rules)."""
    service = get_sheets_service()
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def get_or_create_tab(
    spreadsheet: gspread.Spreadsheet,
    name: str,
    rows: int = 300,
    cols: int = 35,
) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)


def write_formulas(
    spreadsheet: gspread.Spreadsheet,
    tab_name: str,
    data: list[dict],
) -> None:
    """Batch-write formula strings or static values.

    data: list of {"range": "A1", "value": "=FORMULA"} dicts.
    Uses USER_ENTERED so Google Sheets interprets = as a formula.
    """
    batch_data = [
        {"range": f"{tab_name}!{item['range']}", "values": [[item["value"]]]}
        for item in data
        if "value" in item
    ]
    if batch_data:
        spreadsheet.values_batch_update({
            "valueInputOption": "USER_ENTERED",
            "data": batch_data,
        })
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sheets_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sheets_client.py tests/test_sheets_client.py && git commit -m "feat: sheets client factory, batch_update, get_or_create_tab"
```

---

## Task 3: Formula Helpers

Porting the Apps Script helper functions (`colLetter`, `cif`, `metricFormulas`, `writeTable`, `placeFilter`) to Python.

**Files:**
- Create: `src/formula_helpers.py`
- Create: `tests/test_formula_helpers.py`

**Interfaces:**
- Produces: `col_letter(n: int) -> str`
- Produces: `cif(pairs: list[tuple[str,str]], from_ref: str, till_ref: str) -> str`
- Produces: `metric_formulas(base_pairs: list, from_ref: str, till_ref: str) -> list[str]`
- Produces: `write_table(spreadsheet, tab_name, top_row, title, bucket_rows, base_pairs, from_ref, till_ref) -> int`
- Produces: `place_filter(spreadsheet, tab_name, row) -> tuple[str, str]`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_formula_helpers.py
from src.formula_helpers import col_letter, cif, metric_formulas


def test_col_letter_single():
    assert col_letter(1) == "A"
    assert col_letter(26) == "Z"


def test_col_letter_double():
    assert col_letter(27) == "AA"
    assert col_letter(28) == "AB"
    assert col_letter(52) == "AZ"
    assert col_letter(53) == "BA"


def test_cif_structure():
    result = cif([("D", '"*Chandra*"')], "$B$7", "$D$7")
    assert result.startswith("=COUNTIFS(")
    assert "RawData!$B$2:$B" in result
    assert ">=" in result
    assert "<=" in result
    assert '"*Chandra*"' in result


def test_cif_no_extra_pairs():
    result = cif([], "$B$7", "$D$7")
    # Should still have date range filter
    assert "RawData!$B$2:$B" in result
    assert ">=" in result


def test_metric_formulas_count():
    formulas = metric_formulas([], "$B$7", "$D$7")
    assert len(formulas) == 12
    assert all(f.startswith("=COUNTIFS(") for f in formulas)


def test_metric_formulas_with_pairs():
    formulas = metric_formulas([("D", '"*Chandra*"')], "$B$7", "$D$7")
    assert all('"*Chandra*"' in f for f in formulas)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_formula_helpers.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement src/formula_helpers.py**

```python
"""COUNTIFS formula string builders — direct port of Apps Script helpers.

Column references match Apps Script constants:
  B = Date of First Meeting
  C = Opportunity?
  D = Owner?
  F = ICP Segment
  I = Employee Count
  J = Size of Sales Team
  T = Stage
  Y = Conversion (Should Convert)
  Z = Still Active?
"""
import logging
from unittest.mock import MagicMock

logger = logging.getLogger(__name__)


def col_letter(n: int) -> str:
    """Convert 1-based column number to A1-notation letter string."""
    s = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        s = chr(65 + remainder) + s
    return s


def _raw_range(col: str) -> str:
    return f"RawData!${col}$2:${col}"


def cif(pairs: list[tuple[str, str]], from_ref: str, till_ref: str) -> str:
    """Build a COUNTIFS formula string referencing the RawData tab.

    pairs: list of (col_letter, criteria_string)
      criteria_string is either a quoted literal: '"OSS"'
      or a cell reference: '$R$1'
    Date range filter (col B >= from, col B <= till) is always appended.
    """
    all_pairs = list(pairs) + [
        ("B", f'">="&{from_ref}'),
        ("B", f'"<="&{till_ref}'),
    ]
    parts = [f"{_raw_range(col)},{crit}" for col, crit in all_pairs]
    return "=COUNTIFS(" + ",".join(parts) + ")"


def metric_formulas(base_pairs: list[tuple], from_ref: str, till_ref: str) -> list[str]:
    """Return the 12 COUNTIFS formulas for metric columns B–M (Meetings through Pushed Out).

    Matches Apps Script metricFormulas() exactly.
    Ratio formulas (cols N–P: Mtg→Opp%, Opp→Conv%, Mtg→Conv%) are written
    separately in write_table() since they reference the row number.
    """
    bp = base_pairs
    return [
        cif(bp, from_ref, till_ref),                                                              # Meetings
        cif(bp + [("C", '"Yes"')], from_ref, till_ref),                                          # Opportunities
        cif(bp + [("T", '"*trial*"')], from_ref, till_ref),                                      # Trial [POC]
        cif(bp + [("C", '"Yes"'), ("T", '"<>*trial*"'), ("Z", '"Yes"')], from_ref, till_ref),    # Opp, no trial
        cif(bp + [("C", '"No"'), ("T", '"<>*trial*"'), ("Z", '"Yes"')], from_ref, till_ref),     # SQL
        cif(bp + [("T", '"Contract"')], from_ref, till_ref),                                      # In Contract
        cif(bp + [("T", '"Converted"')], from_ref, till_ref),                                     # Converted
        cif(bp + [("Z", '"No"')], from_ref, till_ref),                                            # Inactive
        cif(bp + [("Y", '"Yes"'), ("T", '"<>Converted"'), ("T", '"<>Contract"')], from_ref, till_ref),  # Should Convert
        cif(bp + [("Y", '"Maybe"')], from_ref, till_ref),                                         # Might Convert
        cif(bp + [("Z", '"Yes"')], from_ref, till_ref),                                           # Active Pipeline
        cif(bp + [("Z", '"Pushed Out"')], from_ref, till_ref),                                    # Pushed Out
    ]


def write_table(
    spreadsheet,
    tab_name: str,
    top_row: int,
    title: str,
    bucket_rows: list[tuple[str, list]],
    base_pairs: list[tuple],
    from_ref: str,
    till_ref: str,
) -> int:
    """Write a standard 16-column metric table to the sheet.

    Layout (matches Apps Script writeTable()):
      top_row:   merged title row
      top_row+1: header row (Category + 15 metric headers)
      top_row+2: first data row
      ...
      last+1:    TOTAL row (SUM formulas)
      last+2:    gap row
    Returns the row number of the gap after the table (next section start).
    """
    r = top_row

    batch_data = [
        # Title row
        {"range": f"{tab_name}!A{r}", "values": [[title]]},
        # Header row
        {
            "range": f"{tab_name}!A{r+1}",
            "values": [[
                "Category", "Meetings", "Opportunities", "Trial [POC]",
                "Opp, no trial", "SQL", "In Contract", "Converted",
                "Inactive [Lost + Won]", "Should Convert", "Might Convert",
                "Active Pipeline", "Pushed Out", "Mtg→Opp %", "Opp→Conv %", "Mtg→Conv %",
            ]],
        },
    ]
    r += 2
    first_data_row = r

    for label, extra_pairs in bucket_rows:
        bp = base_pairs + extra_pairs
        formulas = metric_formulas(bp, from_ref, till_ref)
        batch_data.append({
            "range": f"{tab_name}!A{r}:M{r}",
            "values": [[label] + formulas],
        })
        batch_data.append({"range": f"{tab_name}!N{r}", "values": [[f"=IFERROR(C{r}/B{r},0)"]]})
        batch_data.append({"range": f"{tab_name}!O{r}", "values": [[f"=IFERROR(H{r}/C{r},0)"]]})
        batch_data.append({"range": f"{tab_name}!P{r}", "values": [[f"=IFERROR(H{r}/B{r},0)"]]})
        r += 1

    last_data_row = r - 1

    # TOTAL row (SUM of each column)
    total_sums = [
        f"=SUM({col_letter(c)}{first_data_row}:{col_letter(c)}{last_data_row})"
        for c in range(2, 14)  # columns B–M
    ]
    batch_data.append({"range": f"{tab_name}!A{r}", "values": [["TOTAL"]]})
    batch_data.append({"range": f"{tab_name}!B{r}:M{r}", "values": [total_sums]})
    batch_data.append({"range": f"{tab_name}!N{r}", "values": [[f"=IFERROR(C{r}/B{r},0)"]]})
    batch_data.append({"range": f"{tab_name}!O{r}", "values": [[f"=IFERROR(H{r}/C{r},0)"]]})
    batch_data.append({"range": f"{tab_name}!P{r}", "values": [[f"=IFERROR(H{r}/B{r},0)"]]})

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": item["range"], "values": item["values"]} for item in batch_data],
    })

    return r + 2  # +1 for total row, +1 for gap


def place_filter(
    spreadsheet, tab_name: str, row: int
) -> tuple[str, str]:
    """Write From/Till date filter inputs. Returns (from_ref, till_ref) for COUNTIFS."""
    from_ref = f"$B${row}"
    till_ref = f"$D${row}"
    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{tab_name}!A{row}", "values": [["Date filter  ▸  From:"]]},
            {"range": f"{tab_name}!C{row}", "values": [["Till:"]]},
            {"range": f"{tab_name}!B{row}", "values": [["1/1/2026"]]},   # editable
            {"range": f"{tab_name}!D{row}", "values": [["6/30/2026"]]},  # editable
        ],
    })
    return from_ref, till_ref
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_formula_helpers.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/formula_helpers.py tests/test_formula_helpers.py && git commit -m "feat: COUNTIFS formula helpers, write_table, place_filter"
```

---

## Task 4: RawData Tab Builder

Reads all rows from the Sales Pipeline sheet (already maintained by hubspot-sheet-sync) and writes them directly to the RawData tab in the target sheet. This avoids IMPORTRANGE and the Google Sheets authorization prompt that service accounts cannot complete.

**Files:**
- Create: `src/raw_data.py`
- Create: `tests/test_raw_data.py`

**Interfaces:**
- Consumes: `get_gspread_client()`, `get_or_create_tab()`
- Produces: `sync_raw_data(target_spreadsheet: gspread.Spreadsheet) -> int` — returns data rows written

- [ ] **Step 1: Write failing tests**

```python
# tests/test_raw_data.py
from unittest.mock import MagicMock, patch


def test_sync_raw_data_returns_row_count():
    from src.raw_data import sync_raw_data

    mock_source_ws = MagicMock()
    mock_source_ws.get_all_values.return_value = [
        ["Accounts", "Date of First Meeting", "Opportunity?", "Owner?", "Priority"],
        ["example.com", "1/6/2026", "Yes", "Chandra", "High"],
        ["test.io", "8/6/2026", "No", "Piyush", "Low"],
        ["", "", "", "", ""],   # blank row — should be skipped
    ]
    mock_source_ss = MagicMock()
    mock_source_ss.worksheet.return_value = mock_source_ws

    mock_target_ws = MagicMock()
    mock_target_ss = MagicMock()

    with patch("src.raw_data.get_gspread_client") as mock_client, \
         patch("src.raw_data.get_or_create_tab", return_value=mock_target_ws):
        mock_client.return_value.open_by_key.return_value = mock_source_ss
        rows = sync_raw_data(mock_target_ss)

    assert rows == 2
    mock_target_ws.clear.assert_called_once()


def test_sync_raw_data_empty_source():
    from src.raw_data import sync_raw_data

    mock_source_ws = MagicMock()
    mock_source_ws.get_all_values.return_value = []
    mock_source_ss = MagicMock()
    mock_source_ss.worksheet.return_value = mock_source_ws

    mock_target_ss = MagicMock()
    mock_target_ws = MagicMock()

    with patch("src.raw_data.get_gspread_client") as mock_client, \
         patch("src.raw_data.get_or_create_tab", return_value=mock_target_ws):
        mock_client.return_value.open_by_key.return_value = mock_source_ss
        rows = sync_raw_data(mock_target_ss)

    assert rows == 0
    mock_target_ws.clear.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_raw_data.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement src/raw_data.py**

```python
"""Sync RawData tab: read Sales Pipeline sheet → write to target spreadsheet.

Reads hubspot-sheet-sync's output sheet (Sales Pipeline 2026) and copies all
rows to the RawData tab in the target AE KPI tracker sheet. All COUNTIFS
formulas in the analysis tabs reference RawData, so they auto-compute once
this tab is populated.
"""
import logging
import os
import time

import gspread

from src.sheets_client import get_gspread_client, get_or_create_tab

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500


def sync_raw_data(target_spreadsheet: gspread.Spreadsheet) -> int:
    """Copy all rows from Sales Pipeline sheet to RawData tab in target.

    Returns number of data rows written (excluding header).
    Skips rows where column A (domain) is blank.
    """
    source_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    source_tab = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    if not source_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set")

    client = get_gspread_client()
    source_ss = client.open_by_key(source_id)
    source_ws = source_ss.worksheet(source_tab)

    logger.info("Reading source pipeline from '%s'...", source_tab)
    all_rows = source_ws.get_all_values()

    if not all_rows:
        logger.warning("Source pipeline tab is empty — skipping RawData sync")
        return 0

    header = all_rows[0]
    data_rows = [row for row in all_rows[1:] if row and row[0].strip()]

    logger.info("Pipeline: %d data rows (+ 1 header)", len(data_rows))

    raw_ws = get_or_create_tab(target_spreadsheet, "RawData", rows=max(len(data_rows) + 10, 300), cols=40)
    raw_ws.clear()

    raw_ws.update([header], "A1", value_input_option="USER_ENTERED")

    for i in range(0, len(data_rows), _CHUNK_SIZE):
        chunk = data_rows[i : i + _CHUNK_SIZE]
        start_row = i + 2  # row 1 = header, data starts at row 2
        raw_ws.update(chunk, f"A{start_row}", value_input_option="USER_ENTERED")
        logger.info("  wrote rows %d–%d", start_row, start_row + len(chunk) - 1)
        time.sleep(0.5)

    logger.info("RawData: header + %d rows written", len(data_rows))
    return len(data_rows)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_raw_data.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/raw_data.py tests/test_raw_data.py && git commit -m "feat: raw data tab sync (direct copy from sales pipeline)"
```

---

## Task 5: Pipeline Analysis Tabs

Ports `buildDash()`, `buildSummary()`, `buildDimTab()`, `buildAE()`, `buildSecondary()` from the Apps Script to Python.

**Files:**
- Create: `src/pipeline_tabs.py`
- Create: `tests/test_pipeline_tabs.py`

**Interfaces:**
- Consumes: `formula_helpers`, `sheets_client`, `config`
- Produces: `build_all_pipeline_tabs(spreadsheet: gspread.Spreadsheet) -> None`

- [ ] **Step 1: Write failing test**

```python
# tests/test_pipeline_tabs.py
from unittest.mock import MagicMock, patch


def test_build_all_pipeline_tabs_creates_expected_tabs():
    from src.pipeline_tabs import build_all_pipeline_tabs

    mock_ss = MagicMock()
    mock_ws = MagicMock()
    mock_ws.id = 12345

    created_tabs = []

    def fake_get_or_create(ss, name, **kwargs):
        created_tabs.append(name)
        return mock_ws

    with patch("src.pipeline_tabs.get_or_create_tab", side_effect=fake_get_or_create), \
         patch("src.pipeline_tabs.batch_update"), \
         patch("src.pipeline_tabs.write_table", return_value=20), \
         patch("src.pipeline_tabs.place_filter", return_value=("$B$7", "$D$7")):
        build_all_pipeline_tabs(mock_ss)

    assert "Dashboard" in created_tabs
    assert "Summary" in created_tabs
    assert "By ICP Size" in created_tabs
    assert "By ICP Type" in created_tabs
    assert "Chandra" in created_tabs
    assert "Harsha" in created_tabs
    assert "Secondary Owners" in created_tabs
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_pipeline_tabs.py -v
```

- [ ] **Step 3: Implement src/pipeline_tabs.py**

```python
"""Build all formula-based pipeline analysis tabs.

Ported from Apps Script: buildDash(), buildSummary(), buildDimTab(), buildAE(),
buildSecondary(). Each function writes formula strings + static labels. Formatting
(colors, font, widths) is applied via Sheets API v4 batchUpdate.
"""
import logging

import gspread

from src.config import AES, SECONDARY_AES, SIZE_BUCKETS, TYPE_BUCKETS
from src.formula_helpers import write_table, place_filter
from src.sheets_client import get_or_create_tab, batch_update

logger = logging.getLogger(__name__)

_OWNER_DD = ["All", "Chandra", "Harsha", "Akanksha", "Ayush", "Suman", "Deeksha", "Piyush", "Abhishek"]
_ROLE_DD = ["All", "Primary", "Secondary"]
_TYPES_DD = ["All", "OSS", "OSS Affiliated", "Closed Source"]


def _dropdown_request(sheet_id: int, row: int, col: int, options: list[str]) -> dict:
    """Build a setDataValidation batchUpdate request for a single cell dropdown."""
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row - 1, "endRowIndex": row,
                "startColumnIndex": col - 1, "endColumnIndex": col,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in options],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }
    }


def build_dashboard(spreadsheet: gspread.Spreadsheet) -> None:
    """Build Dashboard tab — Owner × ICP Type filter + By ICP Size + By ICP Type tables."""
    ws = get_or_create_tab(spreadsheet, "Dashboard")
    ws.clear()
    tab = "Dashboard"

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{tab}!A1", "values": [["Owner × ICP Type Pipeline Dashboard"]]},
            {"range": f"{tab}!A3", "values": [["Select Owner ▼"]]},
            {"range": f"{tab}!C3", "values": [["All"]]},
            {"range": f"{tab}!A5", "values": [["Select ICP Type ▼"]]},
            {"range": f"{tab}!C5", "values": [["All"]]},
            # Wildcard helper cells (off-screen, referenced by COUNTIFS)
            {"range": f"{tab}!R1", "values": [['=IF($C$3="All","*","*"&$C$3&"*")']]},
            {"range": f"{tab}!R2", "values": [['=IF($C$5="All","*",$C$5)']]},
            {"range": f"{tab}!G3", "values": [['="Showing:  "&C3&"   ·   "&C5&" segment"']]},
            {"range": f"{tab}!A8", "values": [["Owner filter credits each deal the owner is named on. Tables segment by ICP Size buckets & ICP Type and respect the date range above."]]},
        ],
    })

    from_ref, till_ref = place_filter(spreadsheet, tab, 7)

    # Owner + type filter pairs — D col = Owner, F col = ICP Segment
    owner_type_pairs = [("D", "$R$1"), ("F", "$R$2")]
    next_row = write_table(spreadsheet, tab, 10, "By ICP Size  (filtered by Owner × ICP Type)", SIZE_BUCKETS, owner_type_pairs, from_ref, till_ref)
    write_table(spreadsheet, tab, next_row, "By ICP Type  (filtered by Owner)", TYPE_BUCKETS, [("D", "$R$1")], from_ref, till_ref)

    batch_update(spreadsheet.id, [
        _dropdown_request(ws.id, 3, 3, _OWNER_DD),
        _dropdown_request(ws.id, 5, 3, _TYPES_DD),
    ])
    logger.info("Dashboard tab built")


def build_summary(spreadsheet: gspread.Spreadsheet) -> None:
    """Build Summary tab — whole pipeline by ICP Size and ICP Type."""
    ws = get_or_create_tab(spreadsheet, "Summary")
    ws.clear()
    tab = "Summary"

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"{tab}!A1", "values": [["2026 Pipeline Analysis — Summary"]]}],
    })

    from_ref, till_ref = place_filter(spreadsheet, tab, 2)
    next_row = write_table(spreadsheet, tab, 4, "Whole pipeline by ICP Size", SIZE_BUCKETS, [], from_ref, till_ref)
    write_table(spreadsheet, tab, next_row, "Whole pipeline by ICP Type", TYPE_BUCKETS, [], from_ref, till_ref)
    logger.info("Summary tab built")


def build_dim_tab(
    spreadsheet: gspread.Spreadsheet, name: str, title: str, buckets: list
) -> None:
    """Build By ICP Size or By ICP Type tab — Owner role dropdown + main + per-AE tables."""
    ws = get_or_create_tab(spreadsheet, name)
    ws.clear()
    tab = name

    header_data = [
        {"range": f"{tab}!A1", "values": [[title]]},
        {"range": f"{tab}!A2", "values": [["Owner role ▼"]]},
        {"range": f"{tab}!C2", "values": [["All"]]},
        # Main wildcard: Secondary means "Owner contains comma" (second-listed)
        {"range": f"{tab}!R1", "values": [['=IF($C$2="Secondary","*,*","*")']]},
    ]
    # Per-AE wildcards: Primary = "AE*" (first-listed), Secondary filter
    for i, ae in enumerate(AES):
        header_data.append({"range": f"{tab}!R{2+i}", "values": [[f'=IF($C$2="Primary","{ae}*","*{ae}*")']]})
        header_data.append({"range": f"{tab}!S{2+i}", "values": [[f'=IF($C$2="Secondary","<>{ae}*","*")']]})

    note = ("Owner role drives every table. Per-AE tables: All = AE involved · "
            "Primary = AE first-listed · Secondary = AE assisting. Main table: "
            "All/Primary = whole pipeline · Secondary = deals that have a secondary owner.")
    header_data.append({"range": f"{tab}!A5", "values": [[note]]})
    spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": header_data})

    from_ref, till_ref = place_filter(spreadsheet, tab, 4)

    next_row = write_table(spreadsheet, tab, 6, f"Main — {name}", buckets, [("D", "$R$1")], from_ref, till_ref)
    for i, ae in enumerate(AES):
        ae_pairs = [("D", f"$R${2+i}"), ("D", f"$S${2+i}")]
        next_row = write_table(spreadsheet, tab, next_row, f"{ae} — {name}", buckets, ae_pairs, from_ref, till_ref)

    batch_update(spreadsheet.id, [_dropdown_request(ws.id, 2, 3, _ROLE_DD)])
    logger.info("%s tab built", name)


def build_ae_tab(spreadsheet: gspread.Spreadsheet, ae: str) -> None:
    """Build per-AE dashboard tab — By ICP Size + By ICP Type with role dropdown."""
    ws = get_or_create_tab(spreadsheet, ae)
    ws.clear()
    tab = ae

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{tab}!A1", "values": [[f"{ae}  —  AE dashboard"]]},
            {"range": f"{tab}!A2", "values": [["Choose owner role (All = involved on the deal · Primary = first-listed · Secondary = assisting), then the date range."]]},
            {"range": f"{tab}!A3", "values": [["Owner role ▼"]]},
            {"range": f"{tab}!C3", "values": [["All"]]},
            {"range": f"{tab}!R3", "values": [[f'=IF($C$3="Primary","{ae}*","*{ae}*")']]},
            {"range": f"{tab}!S3", "values": [[f'=IF($C$3="Secondary","<>{ae}*","*")']]},
        ],
    })

    from_ref, till_ref = place_filter(spreadsheet, tab, 5)
    ae_pairs = [("D", "$R$3"), ("D", "$S$3")]
    next_row = write_table(spreadsheet, tab, 7, "By ICP Size", SIZE_BUCKETS, ae_pairs, from_ref, till_ref)
    write_table(spreadsheet, tab, next_row, "By ICP Type", TYPE_BUCKETS, ae_pairs, from_ref, till_ref)
    batch_update(spreadsheet.id, [_dropdown_request(ws.id, 3, 3, _ROLE_DD)])
    logger.info("%s AE tab built", ae)


def build_secondary_owners(spreadsheet: gspread.Spreadsheet) -> None:
    """Build Secondary Owners tab — assisting contribution per person (excludes Deeksha, Piyush)."""
    ws = get_or_create_tab(spreadsheet, "Secondary Owners")
    ws.clear()
    tab = "Secondary Owners"

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{tab}!A1", "values": [["Secondary Owners — support contribution"]]},
            {"range": f"{tab}!A2", "values": [["Deals where the person is named but is NOT the primary owner (assisting). Excludes Deeksha & Piyush."]]},
        ],
    })

    from_ref, till_ref = place_filter(spreadsheet, tab, 3)
    sec_rows = [(ae, [("D", f'"*{ae}*"'), ("D", f'"<>{ae}*"')]) for ae in SECONDARY_AES]
    write_table(spreadsheet, tab, 5, "Secondary (assisting) deals by person", sec_rows, [], from_ref, till_ref)
    logger.info("Secondary Owners tab built")


def build_all_pipeline_tabs(spreadsheet: gspread.Spreadsheet) -> None:
    """Build all pipeline analysis tabs in the order defined by the Apps Script."""
    build_dashboard(spreadsheet)
    build_summary(spreadsheet)
    build_dim_tab(spreadsheet, "By ICP Size", "Pipeline by ICP Size  (Enterprise / Commercial / SMB / Startup)", SIZE_BUCKETS)
    build_dim_tab(spreadsheet, "By ICP Type", "Pipeline by ICP Type", TYPE_BUCKETS)
    for ae in AES:
        build_ae_tab(spreadsheet, ae)
    build_secondary_owners(spreadsheet)
    logger.info("All pipeline analysis tabs built")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_pipeline_tabs.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline_tabs.py tests/test_pipeline_tabs.py && git commit -m "feat: pipeline analysis tabs (Dashboard, Summary, By ICP Size/Type, AE×7, Secondary Owners)"
```

---

## Task 6: Weekly Tracker Sync

> **⚠️ Requires Q3 and Q4 answers before full implementation.**
> The skeleton below handles tab discovery and color-count helpers. The write logic is stubbed until source structure is confirmed.

**Key observations from screenshots:**
- "Sales Weekly Updates - 2026" has tabs named like "ws 27 July 2026", "ws 03 Aug 2026"
- The Weekly tracker in the AE KPI tracker shows all weeks as columns (1 Jun, 8 Jun, …)
- Count rows use different size buckets than the pipeline tabs: Enterprise / **Mid Market** / **SMB - Green Flag** / **SMB - Black Flag** / **Non-ICP** (manually color-coded, not employee count thresholds)
- OSS / OSS Affiliated / Non OSS font colors match `WBO` palette
- COUNTBYFILL / COUNTBYFONT custom functions in the source count by cell color

**Files:**
- Create: `src/weekly_sync.py`
- Create: `tests/test_weekly_sync.py`

**Interfaces:**
- Produces: `get_latest_week_tab(tab_names: list[str]) -> str | None`
- Produces: `count_by_bg_color(cells: list[dict], target: dict, tolerance: float) -> int`
- Produces: `count_by_font_color(cells: list[dict], target: dict, tolerance: float) -> int`
- Produces: `sync_weekly_tracker(target_spreadsheet) -> dict`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_weekly_sync.py
from src.weekly_sync import get_latest_week_tab, count_by_bg_color, count_by_font_color


def test_get_latest_week_tab_parses_dates():
    tabs = ["ws 27 July 2026", "ws 03 Aug 2026", "Imported Research forms - SDR", "All Meetings Done - 2026"]
    latest = get_latest_week_tab(tabs)
    assert latest == "ws 03 Aug 2026"


def test_get_latest_week_tab_single():
    assert get_latest_week_tab(["ws 27 July 2026"]) == "ws 27 July 2026"


def test_get_latest_week_tab_no_ws_tabs():
    assert get_latest_week_tab(["Sheet1", "Sheet2"]) is None


def test_count_by_bg_color_exact():
    enterprise_blue = {"red": 0.788, "green": 0.855, "blue": 0.973}
    cells = [
        {"effectiveFormat": {"backgroundColor": enterprise_blue}},
        {"effectiveFormat": {"backgroundColor": enterprise_blue}},
        {"effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}},
    ]
    assert count_by_bg_color(cells, enterprise_blue) == 2


def test_count_by_bg_color_tolerance():
    target = {"red": 0.788, "green": 0.855, "blue": 0.973}
    # Slight rounding difference from Sheets API
    close = {"red": 0.790, "green": 0.856, "blue": 0.972}
    cells = [{"effectiveFormat": {"backgroundColor": close}}]
    assert count_by_bg_color(cells, target, tolerance=0.05) == 1


def test_count_by_font_color():
    red_oss = {"red": 0.8, "green": 0.0, "blue": 0.0}
    cells = [
        {"effectiveFormat": {"textFormat": {"foregroundColor": red_oss}}},
        {"effectiveFormat": {"textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}}}},
    ]
    assert count_by_font_color(cells, red_oss) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_weekly_sync.py -v
```

- [ ] **Step 3: Implement src/weekly_sync.py**

```python
"""Weekly tracker sync — reads from Sales Weekly Updates, writes to target.

Color palette reference (matches Apps Script WBO constants):
  Background colors (ICP Size, manually applied in source):
    Enterprise:       #C9DAF8  rgb(0.788, 0.855, 0.973)
    Mid Market:       #D9D2E9  rgb(0.851, 0.824, 0.914)
    SMB - Green Flag: #D9EAD3  rgb(0.851, 0.918, 0.827)
    SMB - Black Flag: #FCE5CD  rgb(0.988, 0.898, 0.804)
    Non-ICP:          white / default (no background)
  Font colors (ICP Segment):
    OSS:              #000000  black
    OSS Affiliated:   #666666  dark grey
    Non OSS:          #CC0000  red
"""
import logging
import os
import re
from datetime import datetime

import gspread

from src.sheets_client import get_gspread_client, get_sheets_service, get_or_create_tab

logger = logging.getLogger(__name__)

BG_COLORS = {
    "Enterprise":       {"red": 0.788, "green": 0.855, "blue": 0.973},
    "Mid Market":       {"red": 0.851, "green": 0.824, "blue": 0.914},
    "SMB - Green Flag": {"red": 0.851, "green": 0.918, "blue": 0.827},
    "SMB - Black Flag": {"red": 0.988, "green": 0.898, "blue": 0.804},
}
FONT_COLORS = {
    "OSS":            {"red": 0.0, "green": 0.0, "blue": 0.0},
    "OSS Affiliated": {"red": 0.4, "green": 0.4, "blue": 0.4},
    "Non OSS":        {"red": 0.8, "green": 0.0, "blue": 0.0},
}


def _color_close(a: dict, b: dict, tolerance: float = 0.05) -> bool:
    return all(abs(a.get(k, 1.0) - b.get(k, 1.0)) <= tolerance for k in ["red", "green", "blue"])


def count_by_bg_color(cells: list[dict], target: dict, tolerance: float = 0.05) -> int:
    """Count cells whose background color matches target (within tolerance)."""
    return sum(
        1 for cell in cells
        if _color_close(
            cell.get("effectiveFormat", {}).get("backgroundColor", {}),
            target, tolerance
        )
    )


def count_by_font_color(cells: list[dict], target: dict, tolerance: float = 0.05) -> int:
    """Count cells whose font color matches target (within tolerance)."""
    return sum(
        1 for cell in cells
        if _color_close(
            cell.get("effectiveFormat", {}).get("textFormat", {}).get("foregroundColor", {}),
            target, tolerance
        )
    )


def get_latest_week_tab(tab_names: list[str]) -> str | None:
    """Return the name of the most recent 'ws DD Mon YYYY' tab."""
    ws_tabs = []
    for name in tab_names:
        m = re.match(r"ws\s+(\d{1,2}\s+\w+\s+\d{4})", name, re.IGNORECASE)
        if not m:
            continue
        date_str = m.group(1).strip()
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                ws_tabs.append((dt, name))
                break
            except ValueError:
                continue
    if not ws_tabs:
        return None
    return sorted(ws_tabs)[-1][1]


def sync_weekly_tracker(target_spreadsheet: gspread.Spreadsheet) -> dict:
    """Read latest week from Sales Weekly Updates → write Weekly tracker tab.

    Full implementation pending Q3/Q4 clarification. Current version:
    1. Finds the latest 'ws DD Mon YYYY' tab
    2. Reads cell values + colors via Sheets API v4 includeGridData
    3. Returns metadata; write logic to be completed after Q3/Q4 answered

    Returns: {"tab": str, "rows_read": int}
    """
    weekly_id = os.getenv("WEEKLY_SHEET_ID", "").strip()
    if not weekly_id:
        raise RuntimeError("WEEKLY_SHEET_ID is not set")

    client = get_gspread_client()
    source_ss = client.open_by_key(weekly_id)
    tab_names = [ws.title for ws in source_ss.worksheets()]

    latest_tab = get_latest_week_tab(tab_names)
    if not latest_tab:
        logger.warning("No 'ws DD Mon YYYY' tabs found in Sales Weekly Updates")
        return {"tab": None, "rows_read": 0}

    logger.info("Latest weekly tab: '%s'", latest_tab)

    service = get_sheets_service()
    result = service.spreadsheets().get(
        spreadsheetId=weekly_id,
        ranges=[f"'{latest_tab}'!A1:Z300"],
        includeGridData=True,
    ).execute()

    row_data = result["sheets"][0]["data"][0].get("rowData", [])
    logger.info("Read %d rows from '%s' (with color data)", len(row_data), latest_tab)

    # TODO after Q3/Q4: parse structure, compute color-based counts, write to target
    # _write_weekly_tracker_tab(target_spreadsheet, row_data, latest_tab)

    return {"tab": latest_tab, "rows_read": len(row_data)}
```

- [ ] **Step 4: Run tests (runnable subset)**

```bash
pytest tests/test_weekly_sync.py -v
```

Expected: All helper tests PASS; `sync_weekly_tracker` tests skipped until Q3/Q4

- [ ] **Step 5: Commit**

```bash
git add src/weekly_sync.py tests/test_weekly_sync.py && git commit -m "feat: weekly sync skeleton + color-count helpers (Q3/Q4 pending)"
```

---

## Task 7: Weekly - By Owner Tab

> **⚠️ Section A-H row offsets require Q5 answer. Skeleton below covers owner dropdown + conditional formatting rules.**

**Files:**
- Create: `src/weekly_by_owner.py`
- Create: `tests/test_weekly_by_owner.py`

**Interfaces:**
- Produces: `build_weekly_by_owner(spreadsheet: gspread.Spreadsheet) -> None`

- [ ] **Step 1: Write failing test**

```python
# tests/test_weekly_by_owner.py
from unittest.mock import MagicMock, patch


def test_build_weekly_by_owner_calls_batch_update():
    from src.weekly_by_owner import build_weekly_by_owner

    mock_ss = MagicMock()
    mock_ws = MagicMock()
    mock_ws.id = 99

    with patch("src.weekly_by_owner.get_or_create_tab", return_value=mock_ws), \
         patch("src.weekly_by_owner.batch_update") as mock_bu:
        build_weekly_by_owner(mock_ss)

    mock_bu.assert_called()


def test_build_weekly_by_owner_writes_owner_control():
    from src.weekly_by_owner import build_weekly_by_owner

    mock_ss = MagicMock()
    mock_ws = MagicMock()
    mock_ws.id = 99

    with patch("src.weekly_by_owner.get_or_create_tab", return_value=mock_ws), \
         patch("src.weekly_by_owner.batch_update"):
        build_weekly_by_owner(mock_ss)

    # values_batch_update should have been called with Owner: + AF1 wildcard
    calls = mock_ss.values_batch_update.call_args_list
    all_ranges = [item["range"] for call in calls for item in call.args[0].get("data", [])]
    assert any("A1" in r for r in all_ranges)
    assert any("B1" in r for r in all_ranges)
    assert any("AF1" in r for r in all_ranges)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_weekly_by_owner.py -v
```

- [ ] **Step 3: Implement src/weekly_by_owner.py**

```python
"""Build Weekly - By Owner tab — owner-filtered view of the Weekly tracker.

Matches Apps Script buildWeeklyByOwner(). Owner dropdown in B1 drives
COUNTIFS + FILTER formulas that reference the 'Weekly tracker' tab and
RawData. Color coding applied via conditional formatting rules.

Section A-H row offsets use the same values as WBO_SECTIONS in the Apps
Script (shifted +1 for the Owner control row). Full section content
(rows A-H) requires Q5 clarification on row structure.
"""
import logging

import gspread

from src.config import AES
from src.formula_helpers import col_letter
from src.sheets_client import get_or_create_tab, batch_update

logger = logging.getLogger(__name__)

_TAB = "Weekly - By Owner"
_OWNER_OPTIONS = ["All", "Chandra", "Harsha", "Akanksha", "Ayush", "Suman", "Deeksha", "Piyush", "Abhishek"]
_FIRST_WEEK_COL = 3   # col C is the first week column
_LAST_WEEK_COL = 29   # col AC is the last (27 weeks, matching Apps Script WBO_LASTCOL)

_SIZE_BG = {
    "Enterprise":  {"red": 0.788, "green": 0.855, "blue": 0.973},
    "Commercial":  {"red": 0.851, "green": 0.824, "blue": 0.914},
    "SMB":         {"red": 0.851, "green": 0.918, "blue": 0.827},
    "Startup":     {"red": 0.988, "green": 0.898, "blue": 0.804},
}
_SEG_FC = {
    "OSS":            {"red": 0.0, "green": 0.0, "blue": 0.0},
    "OSS Affiliated": {"red": 0.4, "green": 0.4, "blue": 0.4},
    "Closed Source":  {"red": 0.8, "green": 0.0, "blue": 0.0},
}


def build_weekly_by_owner(spreadsheet: gspread.Spreadsheet) -> None:
    """Build the Weekly - By Owner tab."""
    ws = get_or_create_tab(spreadsheet, _TAB, rows=400, cols=120)
    ws.clear()

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{_TAB}!A1", "values": [["Owner:"]]},
            {"range": f"{_TAB}!B1", "values": [["All"]]},
            # AF1 = wildcard pattern used by all section COUNTIFS + FILTER formulas
            {"range": f"{_TAB}!AF1", "values": [['=IF($B$1="All","*","*"&$B$1&"*")']]},
        ],
    })

    # Owner dropdown
    batch_update(spreadsheet.id, [{
        "setDataValidation": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 1, "endColumnIndex": 2,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in _OWNER_OPTIONS],
                },
                "showCustomUi": True, "strict": True,
            },
        },
    }])

    _apply_conditional_formatting(spreadsheet, ws)
    logger.info("Weekly - By Owner tab built (skeleton; sections A-H pending Q5)")


def _apply_conditional_formatting(
    spreadsheet: gspread.Spreadsheet, ws: gspread.Worksheet
) -> None:
    """Apply ICP Size background + Segment font-color CF rules.

    Hidden helper columns (SIZE label and SEGMENT label from VLOOKUP on RawData)
    drive the rules — matching Apps Script weeklyByOwnerColorRulesFor().
    SIZE helper: first_week_col + 40  = col 43 = AQ
    SEG helper:  first_week_col + 80  = col 83 = CE
    """
    size_col = col_letter(_FIRST_WEEK_COL + 40)  # AQ
    seg_col  = col_letter(_FIRST_WEEK_COL + 80)  # CE

    list_top = 19      # first domain list row (Section A, shifted +1 from Apps Script)
    list_bottom = 300  # cover all 8 sections

    range_spec = {
        "sheetId": ws.id,
        "startRowIndex": list_top - 1,
        "endRowIndex": list_bottom,
        "startColumnIndex": _FIRST_WEEK_COL - 1,
        "endColumnIndex": _LAST_WEEK_COL,
    }

    rules = []
    # Combined size + segment rules (most specific — applied first)
    for size_label, size_bg in _SIZE_BG.items():
        for seg_label, seg_fc in _SEG_FC.items():
            rules.append({
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [range_spec],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": f'=AND({size_col}{list_top}="{size_label}",{seg_col}{list_top}="{seg_label}")'}],
                            },
                            "format": {
                                "backgroundColor": size_bg,
                                "textFormat": {"foregroundColor": seg_fc},
                            },
                        },
                    }
                }
            })

    # Fallback: size only
    for size_label, size_bg in _SIZE_BG.items():
        rules.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [range_spec],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'={size_col}{list_top}="{size_label}"'}]},
                        "format": {"backgroundColor": size_bg},
                    },
                }
            }
        })

    # Fallback: segment only
    for seg_label, seg_fc in _SEG_FC.items():
        rules.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [range_spec],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'={seg_col}{list_top}="{seg_label}"'}]},
                        "format": {"textFormat": {"foregroundColor": seg_fc}},
                    },
                }
            }
        })

    if rules:
        batch_update(spreadsheet.id, rules)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_weekly_by_owner.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/weekly_by_owner.py tests/test_weekly_by_owner.py && git commit -m "feat: weekly by owner tab skeleton + conditional formatting rules"
```

---

## Task 8: Setup Script + Sync Orchestrator

**Files:**
- Create: `src/setup.py`
- Create: `src/sync.py`
- Create: `tests/test_sync.py`

**Interfaces:**
- Produces: `run_setup() -> None`
- Produces: `run_sync(dry_run: bool) -> RunResult`

- [ ] **Step 1: Write failing test**

```python
# tests/test_sync.py
from datetime import datetime
from src.sync import RunResult


def test_run_result_fields():
    r = RunResult(
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
        raw_rows=150,
        weekly_tab="ws 03 Aug 2026",
        errors=[],
    )
    assert r.raw_rows == 150
    assert r.weekly_tab == "ws 03 Aug 2026"
    assert r.errors == []


def test_run_sync_dry_run():
    from unittest.mock import patch
    from src.sync import run_sync

    with patch("src.sync.get_gspread_client"):
        result = run_sync(dry_run=True)

    assert isinstance(result, RunResult)
    assert result.errors == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_sync.py -v
```

- [ ] **Step 3: Implement src/setup.py**

```python
"""One-shot setup: build full AE KPI tracker sheet structure.

Run once when creating a new target sheet or rebuilding from scratch.
Safe to re-run — each builder clears the tab before writing.
"""
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from src.sheets_client import get_gspread_client
from src.raw_data import sync_raw_data
from src.pipeline_tabs import build_all_pipeline_tabs
from src.weekly_sync import sync_weekly_tracker
from src.weekly_by_owner import build_weekly_by_owner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)


def run_setup() -> None:
    target_id = os.getenv("TARGET_SHEET_ID", "").strip()
    if not target_id:
        raise RuntimeError("TARGET_SHEET_ID is not set")

    client = get_gspread_client()
    ss = client.open_by_key(target_id)

    logger.info("=== AE KPI Tracker Setup ===")
    logger.info("Step 1/4: Syncing RawData tab...")
    sync_raw_data(ss)
    logger.info("Step 2/4: Building pipeline analysis tabs...")
    build_all_pipeline_tabs(ss)
    logger.info("Step 3/4: Syncing Weekly tracker...")
    sync_weekly_tracker(ss)
    logger.info("Step 4/4: Building Weekly - By Owner tab...")
    build_weekly_by_owner(ss)
    logger.info("=== Setup complete ===")


if __name__ == "__main__":
    run_setup()
```

- [ ] **Step 4: Implement src/sync.py**

```python
"""Scheduled sync — refreshes RawData + Weekly tracker data.

Does NOT rebuild the formula structure (that's run_setup()). Just refreshes
the data tabs that need regular updates. Run after hubspot-sheet-sync completes.
"""
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from src.sheets_client import get_gspread_client
from src.raw_data import sync_raw_data
from src.weekly_sync import sync_weekly_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    started_at: datetime
    finished_at: datetime
    raw_rows: int = 0
    weekly_tab: str | None = None
    errors: list[str] = field(default_factory=list)


def run_sync(dry_run: bool = False) -> RunResult:
    started_at = datetime.utcnow()
    errors: list[str] = []
    raw_rows = 0
    weekly_tab = None

    target_id = os.getenv("TARGET_SHEET_ID", "").strip()
    if not target_id:
        errors.append("TARGET_SHEET_ID is not set")
        return RunResult(started_at=started_at, finished_at=datetime.utcnow(), errors=errors)

    try:
        client = get_gspread_client()
        ss = client.open_by_key(target_id)

        if not dry_run:
            try:
                raw_rows = sync_raw_data(ss)
            except Exception as exc:
                logger.error("RawData sync failed: %s", exc, exc_info=True)
                errors.append(f"RawData: {exc}")

            try:
                result = sync_weekly_tracker(ss)
                weekly_tab = result.get("tab")
            except Exception as exc:
                logger.error("Weekly tracker sync failed: %s", exc, exc_info=True)
                errors.append(f"Weekly: {exc}")
        else:
            logger.info("DRY RUN: would sync RawData + Weekly tracker")

    except Exception as exc:
        logger.error("Sync failed: %s", exc, exc_info=True)
        errors.append(str(exc))

    finished_at = datetime.utcnow()
    result = RunResult(
        started_at=started_at, finished_at=finished_at,
        raw_rows=raw_rows, weekly_tab=weekly_tab, errors=errors,
    )
    logger.info("Sync complete — rows=%d, weekly=%s, errors=%d", raw_rows, weekly_tab, len(errors))
    return result
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_sync.py -v
```

Expected: PASS

- [ ] **Step 6: Smoke test the setup script (requires real credentials)**

```bash
cd ~/ae-kpi-tracker
# Copy credentials and fill .env with real sheet IDs
python -m src.setup
```

Expected: All tabs appear in the target Google Sheet with formulas + dropdowns.

- [ ] **Step 7: Commit**

```bash
git add src/setup.py src/sync.py tests/test_sync.py && git commit -m "feat: setup script + sync orchestrator + RunResult"
```

---

## Task 9: FastAPI + Scheduler

Pattern is identical to hubspot-sheet-sync. Adapt those files with minimal changes.

**Files:**
- Create: `src/api.py`
- Create: `src/scheduler.py`
- Create: `main.py`

- [ ] **Step 1: Create src/scheduler.py**

Copy `~/hubspot-sheet-sync/src/scheduler.py`. Change:
- Import `from src.sync import run_sync, RunResult`
- Schedule trigger to `08:00` UTC (runs after hubspot-sheet-sync at 04:30/16:30 so RawData is fresh)

- [ ] **Step 2: Create src/api.py**

Copy `~/hubspot-sheet-sync/src/api.py`. Key changes:
- `title = "AE KPI Tracker"`
- `PORT = os.getenv("PORT", "8009")`
- Add `/api/setup` endpoint:

```python
@app.post("/api/setup")
async def trigger_setup(background_tasks: BackgroundTasks):
    """Rebuild all tab structures (one-shot)."""
    from src.setup import run_setup
    background_tasks.add_task(run_setup)
    return JSONResponse({"status": "setup started"})
```

- [ ] **Step 3: Create main.py**

```python
import os
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from src.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8009")))
```

- [ ] **Step 4: Run locally**

```bash
cd ~/ae-kpi-tracker && python main.py
```

Open `http://localhost:8009` — dashboard should load. Hit `/api/sync` to test.

- [ ] **Step 5: Commit**

```bash
git add src/api.py src/scheduler.py main.py && git commit -m "feat: FastAPI dashboard + APScheduler (daily 08:00 UTC)"
```

---

## Self-Review

**Spec coverage:**
- ✅ RawData tab — Task 4
- ✅ Dashboard, Summary, By ICP Size, By ICP Type, per-AE (×7), Secondary Owners — Task 5
- ⚠️ Weekly tracker write logic — Task 6 (skeleton; Q3/Q4 required)
- ⚠️ Weekly - By Owner sections A–H — Task 7 (skeleton; Q5 required)
- ✅ Color-count helpers (`count_by_bg_color`, `count_by_font_color`) — Task 6
- ✅ Conditional formatting rules for Weekly - By Owner — Task 7
- ✅ Owner + role dropdowns — Tasks 5, 7
- ✅ Setup script + sync orchestrator — Task 8
- ✅ FastAPI + scheduler — Task 9

**Placeholder check:**
- Tasks 6 and 7 have intentional `# TODO after Q3/Q4/Q5` stubs — these are gated on user answers, not missing design.
- All other tasks have complete, runnable code. ✅

**Type consistency:**
- `SIZE_BUCKETS` / `TYPE_BUCKETS` defined in `config.py`, consumed by `formula_helpers.py` and `pipeline_tabs.py` ✅
- `RunResult` defined in `sync.py`, used only in `sync.py` + `api.py` ✅
- `write_table()` signature: `(spreadsheet, tab_name, top_row, title, bucket_rows, base_pairs, from_ref, till_ref) -> int` — consistent across Task 3 definition and Task 5 usage ✅
- `place_filter()` signature: `(spreadsheet, tab_name, row) -> tuple[str,str]` — consistent ✅
