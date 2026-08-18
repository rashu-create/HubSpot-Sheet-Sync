# Changelog

## 2026-08-10 — Dashboard Alignment Fix + Scheduler Bug Fix

### Fixed — Scheduler never running (RCA + fix)
- **Root cause:** `SCHEDULER_ENABLED=false` was left at its default in the VM `.env` since initial deployment. The scheduler never started, so the 10AM/10PM IST auto-syncs never fired. The service was running but acting as a manual-only dashboard.
- **Fix:** Set `SCHEDULER_ENABLED=true` in `/opt/services/hubspot-sheet-sync/.env` on the VM and restarted the service. Scheduler is now live with both jobs registered (04:30 UTC / 16:30 UTC).
- **Note:** After any deploy that restarts the service, verify this flag is still `true` — it is NOT reset by the deploy, but worth confirming.

### Fixed — Dashboard layout on wide screens
- Wrapped the entire page in `.page-wrapper` (`max-width: 1300px; margin: 0 auto; padding: 0 40px`) so the title, Refresh button, stat cards, and panels all share the same centred column on any screen width. Previously the header was full-width while `.page-content` had `max-width` but no `margin: auto`, causing a blank space on the right on screens wider than 1300px.
- Actions panel: "NEXT SCHEDULED RUN" section now uses `margin-left: auto` to anchor it to the right edge of the panel, with SYNC and DRY RUN remaining on the left.
- Panel sections now vertically centre-align (`align-items: center`).
- Dry Run: replaced visible hint text with a `title` tooltip to keep the panel compact.

---

## 2026-08-06 — Column Normalisation + Sheet Setup Script

### Added — Column normalisation formatters (`src/mapping.py`)
- `fmt_trial_done`: `To-Start` / `to_start` → `"To start"`, `Yes` → `"Yes"`, `No` → `"No"`
- `fmt_l1_qualified`: `Yes` → `Yes`, `Maybe` → `Maybe`, `Weak fit`/`No` → `No`
- `fmt_l2_qualified`: `Yes` → `Yes`, `May be`/`Maybe` → `Maybe`, `No` → `No`
- `fmt_l3_qualified`: `Yes` → `High`, `Maybe` → `Medium`, `No` → `Low`
- Updated `COLUMN_MAP`: cols L, M, N now use the new qualified formatters; col AB uses `trial_done`

### Changed — Formatter dispatch simplified (`src/hubspot.py`)
- Replaced long `if/elif` formatter chain in `get_row_data()` with a single `apply_formatter()` call. New formatters added to `_FORMATTERS` dict in `mapping.py` are now picked up automatically without touching `hubspot.py`.
- Removed unused individual formatter imports.

### Added — Sheet setup script (`scripts/setup_sheet.py`)
- Run once to configure Google Sheet dropdowns and conditional formatting.
- Col T (Stage): dropdown populated with live stage labels from HubSpot Sales Pipeline API.
- Col U (Trial Stage): dropdown with 8 options (To Start, Trial - Integrations, Trial - Ongoing [Hot/Cold], Trial - Ended [Hot/Cold], Data Trial, Trial Not Sure).
- Green row highlight: entire row turns green when col T = "Closed Won".
- Clears existing rules before applying — safe to re-run without accumulating duplicates.

### Deployed
- Files deployed to VM: `src/mapping.py`, `src/hubspot.py`, `src/sync.py`, `scripts/setup_sheet.py`

---

## 2026-08-05 — Column Map Overhaul

### Added — Owner, ICP, L1/L2/L3, Trial Stage, Still Active columns

- **Col D (Owner?):** First names only; multiple owners joined as `"Chandra, Piyush"` — uses `hubspot_owner_id` + `hs_all_owner_ids`
- **Col F (ICP Segment):** `fmt_icp_segment` → `OSS` / `OSS Affiliated` / `Closed Source` / `Agency/Other`; `Non Open Source` → `Closed Source`
- **Col G (ICP Size):** Computed: `Enterprise` ≥500 emp / `Commercial` ≥200 / `SMB` <200 & ≥2 sales team / `Startup`
- **Cols L/M/N:** L1/L2/L3 Qualified — switched source from deal → company properties
- **Cols O/P/Q:** L1/L2/L3 Comments — switched source from deal → company properties
- **Cols R/S:** Next Steps / Due Date — management deal field overrides company form field
- **Col U (Trial Stage):** New column inserted from `trial_status`; all columns U+ shifted right by 1
- **Col AA (Still active?):** Computed — `No` if won/lost/converted, `Pushed Out` if pushed out, else `Yes`
- **Col AG (SDR):** Fixed hardcoded column `AF` → `AG` after the column insertion shifted it

---

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
