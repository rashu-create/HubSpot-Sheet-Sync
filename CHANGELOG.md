# Changelog

## 2026-08-05 — HubSpot Lookup Algorithm Overhaul

### Problem
First production sync matched only 295/326 rows (31 misses). Several companies with active Sales Pipeline deals were not being found due to HubSpot data quality issues: wrong domains, duplicate company records, missing company associations.

### Changes

**1. Company name fallback** (`c58d946`)
Added CONTAINS_TOKEN name search when domain search returns no results. Base name is derived from the domain (e.g. `clickhouse.com` → `clickhouse`). Reduced misses from 31 → 22.

**2. Always merge domain + name candidates** (`35a31b6`)
Previously, if domain search found a company (even with no deal), name search was skipped. Changed to always run both searches and merge results. Fixed cases where company A had the domain but no deal, and company B (with the deal) was only reachable by name. Reduced misses from 22 → 20.

**3. Deal-first domain map pre-build** (`e4e5111`)
Instead of searching per-domain, the sync now starts by fetching all 354 Sales Pipeline deals via batch APIs, resolving company associations, and building a `domain → deal` map. `get_row_data()` checks this map first (O(1)), then falls back to company search. More reliable than company→deal direction. Result: 20 → 19 misses.

**4. Company name map** (`faa78e2`)
Some company records have no domain set in HubSpot. Extended the pre-build to also index by cleaned company name (full name + first word). E.g. "ClickHouse, Inc." → keys `clickhouseinc` and `clickhouse`. Result: 19 → 18 misses.

**5. Deal name map** (`58eb46b`)
Some deals are orphans — they exist in the Sales Pipeline with no linked company record. Added a third map indexed by the deal name itself (e.g. deal named "Clickhouse" is found via domain `clickhouse.com` → base key `clickhouse`). Company ID is resolved at lookup time via the association API. Fixed clickhouse.com and others. Result: 18 → 17 misses.

### Final lookup order (per domain, per sync)
1. **Domain map** — company domain matches sheet domain exactly
2. **Company name map** — company has no domain; matched by cleaned name token
3. **Deal name map** — deal is an orphan (no company linked); matched by deal name
4. **Company search fallback** — HubSpot CONTAINS_TOKEN API search (last resort)

### Current state
**309/326 rows synced (94.8%), 17 misses** — confirmed stable across multiple syncs.

Remaining 17 misses are intentional data mismatches (e.g. `linear.app` ≠ `linearb.io`) or companies genuinely not in the Sales Pipeline.

---

## 2026-08-04 — Initial Deployment

- First sync run: 326 rows, 295 synced, 31 misses, 1 sheet write error
- Sheet write error resolved: protected range on Google Sheet needed service account exception for `kaori-logger@gen-lang-client-0950775973.iam.gserviceaccount.com`
- Dashboard redesigned to Cron Monitor style (cream background, coloured stat cards, run history table)
- Dry run support added (`POST /api/sync?dry_run=true`)
- Run history with type (SYNC/DRY_RUN) and duration added to `data/run_history.json`
- Service deployed to `sales-production` VM at port 8008, registered as `hubspot-sheet-sync.data.reo.dev`
- GitHub: https://github.com/rashu-create/HubSpot-Sheet-Sync
