"""Google Sheets client for HubSpot→Sheet sync.

Responsibilities:
  - read_pipeline_domains() — read col A of the Sales Pipeline tab
  - read_pipeline_first_meetings() — col B (Date of First Meeting) per row
  - build_sdr_map()         — legacy fallback: scan all tabs of the old Weekly Meetings sheet
  - write_pipeline_rows()   — batch-write formatted values to specific row/col pairs

Auth: service account credentials from GOOGLE_CREDENTIALS_FILE.
Env vars: GOOGLE_CREDENTIALS_FILE, PIPELINE_SHEET_ID, PIPELINE_TAB_NAME, SOURCE_SHEET_ID
"""

import logging
import os
from datetime import date  # noqa: F401  (used in annotations)

import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials

from src.header_guard import HeaderMismatchError, check_headers
from src.mapping import col_letter_to_index
from src.meeting_source import UNKNOWN, norm_domain, src_map, to_date

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Column A must NEVER be written
_FORBIDDEN_COL = "A"


def _get_client() -> gspread.Client:
    """Return an authenticated gspread client."""
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    if not creds_file:
        raise RuntimeError("GOOGLE_CREDENTIALS_FILE is not set")
    creds = Credentials.from_service_account_file(creds_file, scopes=_SCOPES)
    return gspread.authorize(creds)


def read_pipeline_domains() -> list[tuple[int, str]]:
    """Open the pipeline sheet and return [(row_index, domain)] for non-blank col A rows.

    Row index is 1-based (Sheets row number). Starts from row 2 (skips header).
    """
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()

    if not sheet_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set")

    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(tab_name)

    # Fetch entire column A
    col_a_values = worksheet.col_values(1)  # list of strings, 1-indexed in Sheets but 0-indexed here

    domains: list[tuple[int, str]] = []
    # col_a_values[0] is the header row (row 1), data starts at index 1 (row 2)
    for idx, value in enumerate(col_a_values):
        row_number = idx + 1  # 1-based
        if row_number < 2:
            continue  # skip header
        stripped = value.strip() if value else ""
        if stripped:
            domains.append((row_number, stripped))

    logger.info(
        "Pipeline sheet: %d non-blank domain rows found in %r tab", len(domains), tab_name
    )
    return domains


def read_pipeline_first_meetings() -> dict[int, "date | None"]:
    """{sheet row number: Date of First Meeting (col B) or None} for rows 2..end.

    Read unformatted so dates arrive as serial numbers whatever the cell format is.
    """
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    if not sheet_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set")

    worksheet = _get_client().open_by_key(sheet_id).worksheet(tab_name)
    values = worksheet.get("B2:B", value_render_option="UNFORMATTED_VALUE")
    return {idx + 2: to_date(row[0] if row else None) for idx, row in enumerate(values)}


def _pick_column(headers: list[str], *candidates) -> int | None:
    """Index of the first header satisfying the first matching candidate predicate."""
    for matches in candidates:
        for i, h in enumerate(headers):
            if matches(h.strip().lower()):
                return i
    return None


def build_sdr_map(alias_map: dict[str, str] | None = None) -> dict[str, str]:
    """Legacy fallback: scan all tabs of SOURCE_SHEET_ID -> {canonical_domain: source}.

    Only used for pipeline rows the Meeting Booked vs Done analysis has no meeting for.
    - Header row: first row with more than one non-blank cell.
    - Domain column: header "domain", else containing "domain", else "company", else containing it.
    - Source column: header "Source of Meeting" or "SDR" (tabs since 25 May 2026 use the former).
    - Values go through the same vocabulary as the analysis (src_map); "?"/blank are dropped.
    - A blank value never overwrites an earlier one; otherwise the LAST (newest) tab wins.
    - Keys are canonical domains (aliases applied).
    """
    sheet_id = os.getenv("SOURCE_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("SOURCE_SHEET_ID is not set")

    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)

    sdr_map: dict[str, str] = {}

    for worksheet in spreadsheet.worksheets():
        tab_name = worksheet.title
        try:
            all_values = worksheet.get_all_values()
        except Exception as exc:
            logger.warning("Could not read tab %r: %s", tab_name, exc)
            continue

        if not all_values:
            continue

        header_row_idx = None
        headers: list[str] = []
        for i, row in enumerate(all_values):
            if len([c for c in row if c.strip()]) > 1:
                header_row_idx = i
                headers = [h.strip() for h in row]
                break

        if header_row_idx is None:
            logger.warning("Tab %r: no header row found — skipping", tab_name)
            continue

        domain_col_idx = _pick_column(
            headers,
            lambda h: h == "domain",
            lambda h: "domain" in h,
            lambda h: h == "company",
            lambda h: "company" in h,
        )
        sdr_col_idx = _pick_column(headers, lambda h: h == "source of meeting", lambda h: h == "sdr")

        if domain_col_idx is None:
            logger.warning("Tab %r: no domain/company column found — skipping", tab_name)
            continue
        if sdr_col_idx is None:
            logger.warning("Tab %r: no Source of Meeting / SDR column found — skipping", tab_name)
            continue

        added = 0
        for row in all_values[header_row_idx + 1:]:
            while len(row) <= max(domain_col_idx, sdr_col_idx):
                row.append("")

            norm = norm_domain(row[domain_col_idx], alias_map)
            source = src_map(row[sdr_col_idx])
            if not norm or source == UNKNOWN:
                continue
            sdr_map[norm] = source
            added += 1

        logger.info("Tab %r: added/updated %d source mappings", tab_name, added)

    logger.info("Legacy source map built: %d unique domains", len(sdr_map))
    return sdr_map


_LAVENDER = {"red": 0.851, "green": 0.824, "blue": 0.914}   # #d9d2e9, same as the manager's tab
_GREY = {"red": 0.4, "green": 0.4, "blue": 0.4}
_GREEN = {"red": 0.416, "green": 0.659, "blue": 0.31}        # #6aa84f, Closed Won rows (manager's tab)
_CURRENCY = {"type": "CURRENCY", "pattern": "$#,##0"}
_PERCENT = {"type": "PERCENT", "pattern": "0%"}
_BASE_TEXT = {"fontFamily": "Calibri", "fontSize": 10}       # every cell of a forecast tab
_KPI_MONEY_COLS = (0, 2, 4)                                  # closed won / weighted / open
_COL_WIDTHS = [170, 110, 150, 200, 90, 90, 80, 330, 60]      # per block column, px


def write_forecast_tab(
    tab_name: str,
    grid: list[list],
    styles: list[tuple[str, int, int, int]],
    block_width: int,
    gap: int,
    money_cols: list[int],
    col_widths: list[int] | None = None,
    percent_cols: list[int] = (),
) -> None:
    """Replace the entire contents and formatting of a sync-owned forecast tab.

    The tab is owned by the sync: values and formats are cleared, `grid` is written at A1
    (USER_ENTERED so HYPERLINK formulas work), then styles are applied.
    styles: (kind, row, col_start, col_end), 0-based, col_end exclusive.
    Touches ONLY `tab_name` - never the Sales Pipeline 2026 tab or any other.
    `col_widths` (px, per block column) defaults to the Overview layout; `percent_cols` get a 0% format.
    """
    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set")

    spreadsheet = _get_client().open_by_key(sheet_id)
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound as exc:
        raise RuntimeError(f"tab '{tab_name}' not found in the sheet (was it renamed?)") from exc
    widths = col_widths or _COL_WIDTHS
    width = max((len(r) for r in grid), default=0)
    if len(grid) > ws.row_count or width > ws.col_count:
        ws.resize(rows=max(len(grid) + 20, ws.row_count), cols=max(width, ws.col_count))

    gid = ws.id
    whole = {"sheetId": gid}

    def cell_fmt(row: int, c0: int, c1: int, fmt: dict, fields: str) -> dict:
        """`fields` are sub-field paths (e.g. "textFormat.bold") so the Calibri/10 base is never overwritten."""
        mask = ",".join(f"userEnteredFormat.{f}" for f in fields.split(","))
        return {"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "cell": {"userEnteredFormat": fmt}, "fields": mask}}

    ws.clear()
    # Reset every format on the tab, then give the whole sheet Calibri 10.
    requests: list[dict] = [{"repeatCell": {"range": whole, "cell": {"userEnteredFormat": {"textFormat": _BASE_TEXT}},
                                            "fields": "userEnteredFormat"}}]

    # Currency on money columns of every block for the full height, then per-kind styles on top.
    n_blocks = (width + gap) // (block_width + gap) if width else 0
    for b in range(n_blocks):
        off = b * (block_width + gap)
        for mc in money_cols:
            requests.append({"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": len(grid),
                          "startColumnIndex": off + mc, "endColumnIndex": off + mc + 1},
                "cell": {"userEnteredFormat": {"numberFormat": _CURRENCY}},
                "fields": "userEnteredFormat.numberFormat"}})
        for pc in percent_cols:
            requests.append({"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": len(grid),
                          "startColumnIndex": off + pc, "endColumnIndex": off + pc + 1},
                "cell": {"userEnteredFormat": {"numberFormat": _PERCENT}},
                "fields": "userEnteredFormat.numberFormat"}})
        for i, w in enumerate(widths[:block_width]):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": gid, "dimension": "COLUMNS",
                          "startIndex": off + i, "endIndex": off + i + 1},
                "properties": {"pixelSize": w}, "fields": "pixelSize"}})

    for kind, row, c0, c1 in styles:
        if kind in ("title", "subtotal"):
            requests.append(cell_fmt(row, c0, c1, {"textFormat": {"bold": True}}, "textFormat.bold"))
        elif kind == "note":
            requests.append(cell_fmt(row, c0, c1, {"textFormat": {"italic": True, "foregroundColor": _GREY}},
                                     "textFormat.italic,textFormat.foregroundColor"))
        elif kind == "kpi_label":
            requests.append(cell_fmt(row, c0, c1, {"textFormat": {"bold": True, "foregroundColor": _GREY}},
                                     "textFormat.bold,textFormat.foregroundColor"))
        elif kind == "kpi_value":
            requests.append(cell_fmt(row, c0, c1, {"textFormat": {"bold": True}}, "textFormat.bold"))
            for mc in _KPI_MONEY_COLS:
                requests.append(cell_fmt(row, c0 + mc, c0 + mc + 1, {"numberFormat": _CURRENCY}, "numberFormat"))
        elif kind in ("header", "section"):
            requests.append(cell_fmt(row, c0, c1, {"backgroundColor": _LAVENDER, "textFormat": {"bold": True}},
                                     "backgroundColor,textFormat.bold"))
        elif kind == "won":
            requests.append(cell_fmt(row, c0, c1, {"textFormat": {"foregroundColor": _GREEN}},
                                     "textFormat.foregroundColor"))
        elif kind == "money":
            requests.append(cell_fmt(row, c0, c1, {"numberFormat": _CURRENCY}, "numberFormat"))

    spreadsheet.values_batch_update({
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": f"'{tab_name}'!A1", "values": grid}],
    })
    spreadsheet.batch_update({"requests": requests})
    logger.info("%s tab written: %d rows x %d cols, %d format requests",
                tab_name, len(grid), width, len(requests))


def write_pipeline_rows(updates: list[dict]) -> None:
    """Batch-write column values to specific rows in the pipeline sheet.

    updates: list of {"row": int, "values": dict[col_letter, value]}

    - NEVER writes column A.
    - Chunked to max 200 rows per batch call.
    - Builds a single batch_update per chunk.
    - Refuses to write (HeaderMismatchError) if row 1 does not match COLUMN_MAP.
    """
    if not updates:
        logger.info("write_pipeline_rows: no updates to write")
        return

    sheet_id = os.getenv("PIPELINE_SHEET_ID", "").strip()
    tab_name = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()

    if not sheet_id:
        raise RuntimeError("PIPELINE_SHEET_ID is not set")

    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(tab_name)

    # Header guard: column letters are only safe while the layout is what COLUMN_MAP expects.
    written_cols = {c for u in updates for c in u["values"]}
    problems = check_headers(worksheet.row_values(1), sorted(written_cols))
    if problems:
        raise HeaderMismatchError(
            "Header guard: pipeline tab layout changed, nothing written — " + "; ".join(problems)
        )

    chunk_size = 200
    total_cells_written = 0

    for chunk_start in range(0, len(updates), chunk_size):
        chunk = updates[chunk_start : chunk_start + chunk_size]
        data: list[dict] = []

        for update in chunk:
            row_idx: int = update["row"]
            values: dict[str, str] = update["values"]

            for col_letter, value in values.items():
                # Safety guard: never write column A
                if col_letter.upper() == _FORBIDDEN_COL:
                    logger.error(
                        "BUG: attempted to write to column A (row %d) — skipped", row_idx
                    )
                    continue

                col_idx = col_letter_to_index(col_letter)
                a1 = rowcol_to_a1(row_idx, col_idx)
                data.append({"range": f"{tab_name}!{a1}", "values": [[value]]})
                total_cells_written += 1

        if data:
            try:
                spreadsheet.values_batch_update(
                    {"valueInputOption": "USER_ENTERED", "data": data}
                )
                logger.info(
                    "Sheets batch write: %d cells updated (chunk %d–%d of %d rows)",
                    len(data),
                    chunk_start + 1,
                    chunk_start + len(chunk),
                    len(updates),
                )
            except Exception as exc:
                logger.error("Sheets batch_update failed: %s", exc, exc_info=True)
                raise

    logger.info(
        "write_pipeline_rows complete: %d rows, %d total cells written",
        len(updates),
        total_cells_written,
    )
