"""Smoke test: dry-run sync against the real sheet (first 5 domain rows only).

Run from the project root:
    python scripts/smoke_test.py

Reads domains from col A, runs HubSpot lookups, prints what WOULD be written
to the sheet — nothing is actually written.
"""

import sys
import os

# Ensure project root is on PYTHONPATH when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import time
from src import hubspot, sheets
from src.mapping import normalize_domain

LIMIT = 5  # only process first N domain rows


def main():
    logger.info("=== SMOKE TEST (dry_run, first %d rows) ===", LIMIT)

    logger.info("Building SDR map...")
    try:
        sdr_map = sheets.build_sdr_map()
        logger.info("SDR map: %d entries", len(sdr_map))
    except Exception as exc:
        logger.error("SDR map failed: %s", exc)
        sdr_map = {}

    logger.info("Reading pipeline domains...")
    domain_rows = sheets.read_pipeline_domains()
    logger.info("Found %d total domain rows; testing first %d", len(domain_rows), LIMIT)

    domain_rows = domain_rows[:LIMIT]

    hits = 0
    misses = 0
    errors = 0

    for row_idx, domain in domain_rows:
        logger.info("--- Row %d: %r ---", row_idx, domain)
        try:
            row_data = hubspot.get_row_data(domain)
        except Exception as exc:
            logger.error("  ERROR: %s", exc)
            errors += 1
            continue

        if row_data is None:
            logger.info("  MISS: no HubSpot match")
            misses += 1
        else:
            norm = normalize_domain(domain)
            row_data["AF"] = sdr_map.get(norm, "")
            logger.info("  HIT: %d columns populated", len(row_data))
            for col, val in sorted(row_data.items()):
                logger.info("    col %-3s = %r", col, val)
            hits += 1

        time.sleep(0.15)

    logger.info("")
    logger.info("=== SMOKE TEST COMPLETE ===")
    logger.info("Rows tested : %d", len(domain_rows))
    logger.info("Hits        : %d", hits)
    logger.info("Misses      : %d", misses)
    logger.info("Errors      : %d", errors)
    logger.info("Nothing was written to the sheet.")


if __name__ == "__main__":
    main()
