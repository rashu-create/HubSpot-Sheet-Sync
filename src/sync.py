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

import src.forecast as forecast
import src.hubspot as hubspot
import src.meeting_source as meeting_source
import src.next_steps_sync as next_steps_sync
import src.sheets as sheets
from src.mapping import SOURCE_COL

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
    forecast_deals: int = 0          # deals shown on the Sales Forecast tab (all months)
    forecast_weighted: float = 0.0   # their weighted amount
    source_filled: int = 0           # rows that got a Source of meeting this run
    source_analysis: int = 0         #   ...of which from the Meeting Booked vs Done analysis
    source_legacy: int = 0           #   ...of which from the old Weekly Meetings sheet
    source_unresolved: int = 0       # rows with no source anywhere (existing value left alone)
    source_skipped: bool = False     # column skipped this run because a source sheet couldn't be read
    ns_mode: str = "off"             # Next Steps / Due Date write-back mode this run: off | dry_run | on
    ns_updated: int = 0              # fields pushed to HubSpot
    ns_would_update: int = 0         # fields a dry run would push
    ns_conflicts: int = 0            # AE edits overruled because HubSpot changed too
    ns_blocked: int = 0              # edits refused (emptied cell / invalid date)
    ns_errors: int = 0               # deals whose HubSpot update failed


def run_sync(dry_run: bool = False) -> RunResult:
    """Pull pipeline domains, enrich with HubSpot, and write back to the sheet.

    Steps:
      1. Load Source of meeting data (analysis Data tab + legacy sheet)
      2. Read pipeline domains from pipeline sheet
      3. For each row: call HubSpot, inject the source, plan the Next Steps write-back, collect update
      4. Push valid Next Steps / Due Date edits, write all updates to the sheet (unless dry_run)
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
    forecast_result = forecast.ForecastResult()
    source_filled = source_analysis = source_legacy = source_unresolved = 0
    source_skipped = False
    ns = next_steps_sync.NextStepsSync.from_env(sync_dry_run=dry_run)

    try:
        # 1. Source of meeting inputs. A failure skips the source column for this run (it is never
        #    blanked, and the legacy sheet is not used as a stand-in for the analysis).
        analysis = None
        try:
            analysis = meeting_source.load_analysis()
            for problem in analysis.alias_problems:
                errors.append(f"Source of meeting: alias problem — {problem}")
        except Exception as exc:
            logger.error("Failed to load the analysis Data tab: %s", exc, exc_info=True)
            errors.append(f"Source of meeting: analysis read failed — {exc}")

        legacy: dict[str, str] = {}
        try:
            legacy = sheets.build_sdr_map(analysis.alias_map if analysis else None)
        except Exception as exc:
            logger.error("Failed to build the legacy source map: %s", exc, exc_info=True)
            errors.append(f"Legacy source map build failed: {exc}")

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

        # 2b. Pick the source for every row (needs col B, the Date of First Meeting)
        sources: dict[int, tuple[str, str]] | None = None
        if analysis is not None:
            try:
                first_meetings = sheets.read_pipeline_first_meetings()
                sources = meeting_source.resolve_sources(
                    [(row, domain, first_meetings.get(row)) for row, domain in domain_rows],
                    analysis,
                    legacy,
                )
            except Exception as exc:
                logger.error("Failed to resolve Source of meeting: %s", exc, exc_info=True)
                errors.append(f"Source of meeting: could not resolve — {exc}")
        if sources is None:
            source_skipped = True
        else:
            source_filled = len(sources)
            source_analysis = sum(1 for _, rule in sources.values() if rule.startswith("analysis"))
            source_legacy = source_filled - source_analysis
            source_unresolved = rows_total - source_filled

        # 2c. Next Steps / Due Date write-back: layout check, current cells, snapshot (off by default)
        ns.prepare(domain_rows)

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
                ns.add_row(row_idx, domain, {}, None)
                # The source does not depend on HubSpot, so still write it for this row.
                if sources is not None and row_idx in sources:
                    updates.append({"row": row_idx, "values": {SOURCE_COL: sources[row_idx][0]}})
                # Rate floor between rows (rate limiter handles per-call)
                time.sleep(0.15)
                continue

            # Meta keys (underscore) are for the write-back only and are never written to the sheet.
            meta = {k: row_data.pop(k) for k in [k for k in row_data if k.startswith("_")]}
            ns.add_row(row_idx, domain, meta, row_data)

            # Source of meeting (col E): only ever written when resolved — an unresolved or skipped
            # column leaves whatever is already in the sheet.
            row_data.pop(SOURCE_COL, None)
            if sources is not None and row_idx in sources:
                row_data[SOURCE_COL] = sources[row_idx][0]

            updates.append({"row": row_idx, "values": row_data})
            rows_synced += 1

            # Per-row floor sleep (rate limiter handles per-API-call)
            time.sleep(0.15)

        # 5. Write to sheet
        if not dry_run:
            ns.before_write(updates)          # race guard + push valid Next Steps / Due Date edits to HubSpot
            if updates:
                logger.info("Writing %d rows to sheet...", len(updates))
                try:
                    sheets.write_pipeline_rows(updates)
                except Exception as exc:
                    logger.error("Sheet write failed: %s", exc, exc_info=True)
                    errors.append(f"Sheet write failed: {exc}")
            else:
                logger.info("No rows to write (all were misses or errors)")
            ns.after_write()                  # snapshot from what the sheet holds now + decision log
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

    # 6. Sales Forecast tab — isolated: a failure here is recorded in `errors` and never
    #    affects the row sync above. (If the pipeline read failed we returned early, above.)
    try:
        forecast_result = forecast.run_forecast_sync(dry_run=dry_run)
    except Exception as exc:
        logger.error("Forecast refresh failed: %s", exc, exc_info=True)
        errors.append(f"Forecast: {exc}")

    errors.extend(ns.errors)
    ns_summary = ns.summary()
    finished_at = datetime.utcnow()

    result = RunResult(
        started_at=started_at,
        finished_at=finished_at,
        rows_total=rows_total,
        rows_synced=rows_synced,
        rows_skipped=rows_skipped,
        errors=errors,
        misses=misses,
        forecast_deals=forecast_result.deals,
        forecast_weighted=forecast_result.weighted,
        source_filled=source_filled,
        source_analysis=source_analysis,
        source_legacy=source_legacy,
        source_unresolved=source_unresolved,
        source_skipped=source_skipped,
        ns_mode=ns.mode,
        ns_updated=ns_summary["updated"],
        ns_would_update=ns_summary["would_update"],
        ns_conflicts=ns_summary["conflicts"],
        ns_blocked=ns_summary["blocked"],
        ns_errors=ns_summary["errors"],
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
