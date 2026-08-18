"""Sync orchestration for HubSpot→Sheet sync.

run_sync() is the main entry point — called from both the APScheduler jobs
and the FastAPI /api/sync endpoint (via a background thread).

It is a regular (synchronous) function to keep things simple and compatible
with APScheduler's threading model.
"""

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

import src.hubspot as hubspot
import src.sheets as sheets
from src.mapping import normalize_domain

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
    rows_total: int
    rows_synced: int
    rows_skipped: int   # no HubSpot match
    errors: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)  # domains with no HubSpot company


def run_sync(dry_run: bool = False) -> RunResult:
    """Pull pipeline domains, enrich with HubSpot, and write back to the sheet.

    Steps:
      1. Build SDR map from source sheet
      2. Read pipeline domains from pipeline sheet
      3. For each row: call HubSpot, inject SDR value, collect update
      4. Write all updates to sheet (unless dry_run)
      5. Return RunResult

    Errors during individual row lookups are logged and the row is counted as
    skipped; the sync continues for all other rows.
    """
    started_at = datetime.utcnow()
    rows_total = 0
    rows_synced = 0
    rows_skipped = 0
    errors: list[str] = []
    misses: list[str] = []
    updates: list[dict] = []

    try:
        # 1. Build SDR map
        logger.info("Building SDR map from source sheet...")
        try:
            sdr_map = sheets.build_sdr_map()
        except Exception as exc:
            logger.error("Failed to build SDR map: %s", exc, exc_info=True)
            sdr_map = {}
            errors.append(f"SDR map build failed: {exc}")

        # 2. Read pipeline domains
        logger.info("Reading pipeline domains...")
        try:
            domain_rows = sheets.read_pipeline_domains()
        except Exception as exc:
            logger.error("Failed to read pipeline domains: %s", exc, exc_info=True)
            finished_at = datetime.utcnow()
            return RunResult(
                started_at=started_at,
                finished_at=finished_at,
                rows_total=0,
                rows_synced=0,
                rows_skipped=0,
                errors=[f"Pipeline domain read failed: {exc}"],
                misses=[],
            )

        rows_total = len(domain_rows)
        logger.info("Pipeline has %d rows to process", rows_total)

        # 3. Pre-build deal map (deals → companies → domains) for reliable lookup
        try:
            hubspot.prebuild_deal_map()
        except Exception as exc:
            logger.warning("Deal map pre-build failed (will fall back to company search): %s", exc)

        # 4. Process each row
        for row_idx, domain in domain_rows:
            try:
                row_data = hubspot.get_row_data(domain)
            except Exception as exc:
                logger.error("Unexpected error for domain %r: %s", domain, exc, exc_info=True)
                errors.append(f"{domain}: {exc}")
                rows_skipped += 1
                continue

            if row_data is None:
                logger.info("No HubSpot match for domain %r (row %d)", domain, row_idx)
                misses.append(domain)
                rows_skipped += 1
                # Rate floor between rows (rate limiter handles per-call)
                time.sleep(0.15)
                continue

            # Inject SDR value from sdr_map
            norm = normalize_domain(domain)
            row_data["AG"] = sdr_map.get(norm, "")

            updates.append({"row": row_idx, "values": row_data})
            rows_synced += 1

            # Per-row floor sleep (rate limiter handles per-API-call)
            time.sleep(0.15)

        # 5. Write to sheet
        if not dry_run:
            if updates:
                logger.info("Writing %d rows to sheet...", len(updates))
                try:
                    sheets.write_pipeline_rows(updates)
                except Exception as exc:
                    logger.error("Sheet write failed: %s", exc, exc_info=True)
                    errors.append(f"Sheet write failed: {exc}")
            else:
                logger.info("No rows to write (all were misses or errors)")
        else:
            logger.info(
                "DRY RUN: would write %d rows, skip %d rows, %d errors",
                rows_synced,
                rows_skipped,
                len(errors),
            )

    except Exception as exc:
        logger.error("Sync failed unexpectedly: %s", exc, exc_info=True)
        errors.append(f"Unexpected sync failure: {exc}")

    finished_at = datetime.utcnow()

    result = RunResult(
        started_at=started_at,
        finished_at=finished_at,
        rows_total=rows_total,
        rows_synced=rows_synced,
        rows_skipped=rows_skipped,
        errors=errors,
        misses=misses,
    )

    logger.info(
        "Sync %s — total=%d synced=%d skipped=%d errors=%d misses=%d",
        "DRY RUN" if dry_run else "COMPLETE",
        rows_total,
        rows_synced,
        rows_skipped,
        len(errors),
        len(misses),
    )
    return result
