"""Google Sheets client for HubSpot→Sheet sync.

Responsibilities:
  - read_pipeline_domains() — read col A of the Sales Pipeline tab
  - build_sdr_map()         — scan all tabs of the source sheet for SDR mapping
  - write_pipeline_rows()   — batch-write formatted values to specific row/col pairs

Auth: service account credentials from GOOGLE_CREDENTIALS_FILE.
Env vars: GOOGLE_CREDENTIALS_FILE, PIPELINE_SHEET_ID, PIPELINE_TAB_NAME, SOURCE_SHEET_ID
"""

import logging
import os

import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials

from src.mapping import normalize_domain, col_letter_to_index

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


def build_sdr_map() -> dict[str, str]:
    """Scan all tabs of SOURCE_SHEET_ID and build {normalized_domain: sdr_value}.

    Each tab is scanned for a domain/company column and an "SDR" column.
    - Header row detection: first row with more than one non-blank cell.
    - Domain column: column whose header contains "domain" or "company" (case-insensitive).
    - SDR column: column whose header is exactly "SDR" (case-insensitive).
    - On duplicate domain across tabs: prefer the LAST tab (newest) in tab order.
    - Tabs without both domain and SDR columns are skipped with a log.warning.
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

        # Find header row: first row with >1 non-blank cell
        header_row_idx = None
        headers: list[str] = []
        for i, row in enumerate(all_values):
            non_blank = [c for c in row if c.strip()]
            if len(non_blank) > 1:
                header_row_idx = i
                headers = [h.strip() for h in row]
                break

        if header_row_idx is None:
            logger.warning("Tab %r: no header row found — skipping", tab_name)
            continue

        # Find domain column (contains "domain" or "company")
        domain_col_idx: int | None = None
        sdr_col_idx: int | None = None

        for i, h in enumerate(headers):
            h_lower = h.lower()
            if domain_col_idx is None and ("domain" in h_lower or "company" in h_lower):
                domain_col_idx = i
            if sdr_col_idx is None and h_lower == "sdr":
                sdr_col_idx = i

        if domain_col_idx is None:
            logger.warning("Tab %r: no domain/company column found — skipping", tab_name)
            continue
        if sdr_col_idx is None:
            logger.warning("Tab %r: no SDR column found — skipping", tab_name)
            continue

        data_rows = all_values[header_row_idx + 1:]
        added = 0
        for row in data_rows:
            # Extend row if shorter than expected
            while len(row) <= max(domain_col_idx, sdr_col_idx):
                row.append("")

            raw_domain = row[domain_col_idx].strip()
            sdr_value = row[sdr_col_idx].strip()

            if not raw_domain:
                continue

            norm = normalize_domain(raw_domain)
            # Later tabs override earlier ones (prefer newest)
            sdr_map[norm] = sdr_value
            added += 1

        logger.info("Tab %r: added/updated %d SDR mappings", tab_name, added)

    logger.info("SDR map built: %d unique normalised domains", len(sdr_map))
    return sdr_map


def write_pipeline_rows(updates: list[dict]) -> None:
    """Batch-write column values to specific rows in the pipeline sheet.

    updates: list of {"row": int, "values": dict[col_letter, value]}

    - NEVER writes column A.
    - Chunked to max 200 rows per batch call.
    - Builds a single batch_update per chunk.
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
