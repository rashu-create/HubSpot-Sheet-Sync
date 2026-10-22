# Changelog

## 2026-10-22 — PREP (NOT DEPLOYED): "Source of meeting" moves from AG to col E (branch `feat/source-col-move`)

### Changed — `src/mapping.py`, `src/sync.py`, `scripts/setup_sheet.py`, `scripts/source_diff.py`
- `COLUMN_MAP` re-lettered: E = Source of meeting (Muni Sheet) (`SOURCE_COL`), old E..AB are now F..AC (Segment G, Size H, Employee Count J, Sales team K, Next Steps S, Due Date T, Stage U, Conversion AA, Still active? AB, Trial done? AC); AD..AH are manual SKIP columns; AI onward unchanged.
- `sync.py` writes the source to `SOURCE_COL`; `scripts/setup_sheet.py` derives its dropdown columns and the "Closed Won" row highlight (`=$U2="Closed Won"`) from `COLUMN_MAP` instead of hardcoded `T`/`U`; `source_diff.py` reads `SOURCE_COL`.
- The header guard compares row 1 with the NEW layout, so deploying this code before the column is moved only produces "Header guard: …" errors and writes nothing.

### Added — `src/column_move.py`, `scripts/move_source_column.py`
- One-shot move script: `--check` (read-only, reports PRE/POST layout), `--apply --yes` (hidden backup tab -> ONE `moveDimension` AG->E -> cell-by-cell verification against the backup, prints the conditional-format formulas), `--rollback --yes` (E back to AG, compared with the backup). Pure helpers + tests in `tests/test_column_move.py`.
- Runbook: `docs/cutover-source-col-move.md`.

### Tests: 243 (was 228).

---
## 2026-10-22 — Twice-daily schedule, header guard, Source of meeting from the analysis 

### Changed — `src/scheduler.py`
- The sync now runs **twice a day at 10:00 and 22:00 IST (04:30 and 16:30 UTC)** as one job, `sync_twice_daily` (was `sync_daily`, 08:00 UTC). `max_instances=1`, `misfire_grace_time=300`.

### Added — `src/header_guard.py`, `src/sheets.py`
- **Header guard:** before writing, `write_pipeline_rows` reads row 1 and compares the header of every column it is about to write with `COLUMN_MAP` (trimmed, case-insensitive). On any mismatch it writes **nothing** and raises `HeaderMismatchError` ("Header guard: …"), which shows up in the run's errors. Protects against inserted/moved/renamed columns.
- `COLUMN_MAP` header names corrected to the live ones: AG "Source of meeting (Muni Sheet)", AO "Deal Value".

### Changed — Source of meeting (col AG): `src/meeting_source.py`, `src/sync.py`, `src/sheets.py`
- **Why it was blank:** `build_sdr_map()` only read columns headed exactly `SDR`, but every tab since 25 May 2026 uses `Source of Meeting`, so all newer meetings were skipped. Later tabs also overwrote earlier values with blanks, there were no domain aliases, and a failed build blanked the whole column.
- **Now:** the source comes from the Meeting Booked vs Done analysis sheet (`ANALYSIS_SHEET_ID`, tab `Data`, read by header name; aliases from its `Domain Aliases` tab, validated the same way as the analysis does). For each pipeline row the meeting is chosen by: `analysis_exact` (Meeting Date + Days Late = the row's Date of First Meeting) → `analysis_before` (latest meeting on or before it, 1-day slack) → `analysis_earliest` (only if that meeting is within 30 days after the row's date; a meeting months later is a different engagement, so the row falls to the legacy sheet or keeps its value). Rebooked child rows and `Unknown` sources are ignored. Rows with no analysis meeting fall back to the old Weekly Meetings sheet (`legacy`), now fixed (reads `Source of Meeting` or `SDR`, prefers a `Domain` header, blank never overwrites, same vocabulary).
- **Never blanks:** AG is written only when a source was found; unresolved rows keep what is in the sheet. If the analysis sheet or the first-meeting dates can't be read, the column is skipped for that run and an error is recorded (the legacy sheet is not used as a stand-in).
- AG is also written for rows with no HubSpot match (the source doesn't depend on HubSpot).
- Slack success line: "Source of meeting: N filled (x analysis, y legacy), z unresolved"; run history gained `source_*` fields.
- `src_map`, `norm_domain` and the alias validator are copies from meeting-booked-done-analysis @ 637c588 (header comment in `src/meeting_source.py`) — keep in sync.
- `scripts/source_diff.py` (read-only) writes `output/source_diff_*.csv`: every row's current value, new value, rule and change type. First run 2026-10-22: 455 rows → 411 exact, 4 earliest, 16 legacy, 24 unresolved; 202 blank cells would be filled, 38 values would change, 191 unchanged.
- New env var **`ANALYSIS_SHEET_ID`** (add to the VM `.env` before deploying).

### Tests
- 228 tests (was 134): schedule, header guard, `meeting_source` rules, sheet readers, sync integration, Slack/history. `real_sources` marker added; conftest stubs the new Sheets reads.

---
## 2026-10-22 — Project cleanup

### Changed
- Added `README.md` (what it does, run/deploy, column map, **table of downstream consumers of the sheet** to check before any column change).
- Moved finished one-shot scripts to `scripts/archive/` (with a README warning not to re-run them): `insert_qualified_column`, `verify_qualified_column`, `clear_columns`, `diagnose_owner_colD`, `diagnose_graphify`, `discover_properties`.
- `.gitignore`: added `.ruff_cache/` and `.superpowers/`.
- Removed the stray pricing-alerts plan from `docs/superpowers/plans/` (moved to `~/pricing-alerts`).
- Deleted the fully merged branches `deploy/safe-deploy-script` and `feat/deal-amount-column` (local and `origin`).
- No runtime code changed.

---
## 2026-10-12 — "Sales Forecast - AEs" tab; Overview tab renamed

### Added — `src/forecast.py` (`build_ae_grid`), `src/sheets.py`, `src/slack.py`
- New tab **Sales Forecast - AEs**: a summary row per AE (+ Total) followed by one "<AE>'s Pipeline" list per **primary owner**, covering the current and next month (same window and membership rules as the Overview). Columns: Company, Type, Owner, Stage, Amount, Weighted, Close date, Tier, Probability, Next steps, HubSpot link. Rows sort by month, then Closed Won → High → Medium → Low, then amount. **Closed Won rows are green.** Lost / Pushed Out deals are not shown. Weighted = amount x deal-stage probability, as on the Overview.
- Both tabs are built from the **same fetched deals** (one HubSpot fetch per month), so the AE Total always equals the Overview totals. Live check 2026-10-12: 28 deals, $370,200 weighted, $43,800 closed won on both.
- `write_forecast_tab` gained `col_widths` and `percent_cols`, new `won` / `money` style kinds, and a clear error when a tab is missing ("tab '...' not found in the sheet (was it renamed?)").
- Slack success line now reads "Sales Forecast tabs refreshed (Overview + AEs)". An AEs-tab failure is reported as `Forecast: AEs tab: ...`; the Overview is already written by then.

### Changed
- **Formatting (both tabs):** Calibri, size 10 everywhere (title and KPI values used to be 14 / 12). The writer now resets the tab to Calibri 10, and per-style formats use field-level masks so they never overwrite the font.
- **Removed from both tabs:** the "Rewritten from HubSpot on every sync - do not type in this tab." line. **Removed from the Overview:** the Piyush reconciliation footer ("HubSpot's Sales Forecast screen excludes Piyush ..."). Piyush's deals are still included in all totals; only the explanatory note is gone. (`OFF_SCREEN_OWNER` deleted.)
- The Overview tab is now looked up as **`Sales Forecast - Overview`** (was `Sales Forecast`; renamed in the sheet by Rashu). **Until this version is deployed, the VM's 08:00 UTC sync fails at the forecast step with a missing-tab error** (the row sync is unaffected).

### Tests
- 134 tests (was 117): `TestBuildAeGrid` (reconciliation with the Overview blocks, one section per primary owner, each deal once, sort order, green rows only for Closed Won, year-safe title, empty month), `TestWriteForecastTab`, and run-level tests for both tab names / AEs-failure labelling / one fetch per month.

---
## 2026-10-09 — "Sales Forecast" tab, refreshed by every sync

### Added — `src/forecast.py`, `src/sheets.py` (`write_forecast_tab`), `src/sync.py`
- New tab **Sales Forecast** in "Sales Pipeline - 2026 - HubSpot Synced", rebuilt from HubSpot at the end of every sync (daily 08:00 UTC + manual `/api/sync`). Current + next month side by side (rolls on the 1st, year-safe). Layout follows the manager's `Oct'26 Pipeline` tab: KPI strip, Closed till date, High 80% / Medium 50% / Low 20% (subtotals in the section header), per-owner table, reconciliation footer.
- **Read-only against HubSpot** (search + batch reads). The tab is fully owned by the sync: values AND formatting are cleared and rewritten each run, so manual edits in it are lost (the tab says so).
- Rules, verified against HubSpot's own Sales Forecast screen (Oct 2026): deal in Sales Pipeline, `closedate` in the month, and either stage label "Closed Won" or still open with `hs_manual_forecast_category` HIGH/MEDIUM/LOW. Closed Lost / Irrelevant / Pushed Out are excluded whatever their category says. **Weighted = amount x deal-stage probability** (not the tier %), Closed Won at 100%.
- Piyush's deals are included; HubSpot's screen omits him, so the footer reconciles (Oct: screen = 18 deals / $226,200 weighted / $43,800 closed won; with Piyush's Nasiko = 19 / $262,200).
- Stage logic uses stage **labels**, never IDs: id `closedwon` is the *Pricing Discussion* stage in this portal.
- Isolated step: a forecast failure is appended to `errors` as `Forecast: ...` and never stops the row sync. `RunResult` gained `forecast_deals` / `forecast_weighted` (run history + Slack success line).
- Close date is written as text (leading apostrophe) because `USER_ENTERED` otherwise turns "7 Oct" into a date serial.

### Added — `scripts/forecast_delta.py` (read-only, local only, not deployed)
- Compares the manager's `Oct'26 Pipeline` tab (sheet ID from `MANAGER_SHEET_ID` or `~/weekly-sales-tracker/.env` `PIPELINE_SHEET_ID`) with HubSpot's forecast: sheet deals missing from the forecast with a reason code (`NO_CLOSE_DATE`, `OTHER_MONTH`, `STAGE_CLOSED`, `CLOSED_WON_ELSEWHERE`, `CATEGORY_OMIT`, `NO_DEAL`), HubSpot forecast deals missing from the sheet (and whether they sit in its "Broader" section), and tier/amount mismatches. Matches on deal name, company name and company domain. Writes CSVs; writes nothing to HubSpot or either sheet.
- Findings 2026-10-09: 41 sheet deals, 14 in forecast; of 27 missing, 14 had a category but no close date. Re-check 2026-10-12 after AEs updated HubSpot: 26 missing; 8 still without a close date (Stakater, Akamai, Cognee, FastRouter, GitGuardian, CKEditor, Bitdrift, Seventh State).

### Notes
- **Meeting Done is NOT excluded by stage.** Only closed stages (Closed Lost / Irrelevant / Pushed Out / Meeting Didn't Happen) are excluded; an open deal in Meeting Done appears if it has a forecast category and a close date in the month. (Lamatic/Navtech are out because of category OMIT, not stage.)
- The tab refreshes **once a day** (08:00 UTC = 1:30 PM IST), not twice.
- HubSpot forecast categories/close dates change during the day, so any comparison is a point-in-time snapshot (Oct 12: 19 deals, $249,000 weighted incl. Piyush; $213,000 / 18 deals excl. Piyush).

### Tests
- `tests/test_forecast.py` (38 tests). New `tests/conftest.py` autouse fixture stubs `hubspot.prebuild_deal_map` and the forecast step so unit tests never touch HubSpot/Sheets (the old sync tests each did a real pipeline crawl; suite is 117 tests in ~2 s, was ~30 s per sync test). `pytest.ini` registers the `real_forecast` marker.

---
## 2026-10-06 — Stage labels trimmed (`"Trial "` → `"Trial"`)

### Fixed — `src/hubspot.py`
- `_get_stage_labels()` now stores `stage["label"].strip()`. HubSpot's Trial stage label is literally `"Trial "` (trailing space), which was written as-is into col T for 20 deals. Other stage labels are unchanged; `still_active` substring checks are unaffected.

### Notes
- `ae-kpi-tracker` keeps its `"Trial*"` criterion (`_stage_crit()`); it still matches `"Trial"`, so it needs no change. "Trial" is the only one of the 13 stage labels that starts with "Trial".
- No other project matches on the string `"Trial "`.
- Takes effect in the sheet on the next sync (manual `/api/sync` or the 08:00 UTC run).

### Tests
- `TestStageLabelStrip` (79 tests pass).

---

## 2026-10-05 — Employee Count priority reversed: `numberofemployees` first

### Fixed — `src/hubspot.py`, `src/mapping.py`
- `_compute_employee_count_merged` and `_compute_icp_size` now read `numberofemployees` first and fall back to `employee_count`. This **reverses** the 2026-09-01 change below.

### Root cause
The 2026-09-01 change assumed `numberofemployees` was "often blank" and that `employee_count` ("R.Employee count") was kept up to date by the sales team. HubSpot property history showed the opposite (456 pipeline companies):
- `employee_count` is a **one-time CSV IMPORT from Feb 2024** (97 companies, never updated since).
- `numberofemployees` is set for 449 companies, mostly via FORM; 408 were updated in 2026.
- No company had `employee_count` without `numberofemployees`, so the old fallback never helped.
- The two disagreed on 95 companies; `numberofemployees` was newer on 86 (e.g. anyscale.com: 290 stale vs 125 live).

### Impact
- Col I (Employee Count) changes for ~95 companies.
- Col G (ICP Size) changes bucket for 19 companies (e.g. anthropic.com, coder.com → Enterprise; anyscale.com → under 200). `ae-kpi-tracker` "By ICP Size" tabs read col G and shift accordingly.

### Tests
- Flipped the priority tests in `TestEmployeeCountMerge`; added a stale-`employee_count` regression test (78 tests pass).

### Rollout & verification
- Commit `0d68afa`; pushed to `main` together with the three 2026-09-29 Qualified-column commits (previously unpushed). Deployed with `deploy/deploy.sh` at 09:26 UTC.
- Manual sync at 09:28 UTC: 424/430 rows synced, 0 errors, 201s. Verified against HubSpot: all 93 disagreeing companies present in the sheet now show `numberofemployees` in col I (anyscale.com 290 → 125, anthropic.com → 4,345, coder.com → 644); 0 still wrong.
- `ae-kpi-tracker` `/api/sync` run afterwards to refresh the Weekly tracker (analysis tabs are live formulas and recalculated on their own). No formula errors in any tab either service writes. `/api/setup` was not needed (no formula/column change).
- 6 rows skipped on that sync: namespace.so, kilo.ai, corellium.com, amd.com, usegit.ai and scaleops.com. scaleops.com was new on this list and is unrelated to this change (row matching is unaffected); reported fixed by Rashu 2026-10-05 — to be confirmed on the next 08:00 UTC sync.

---

## 2026-09-29 — "Qualified" column inserted at AI; AI–AN shifted right by one

### Added — `src/hubspot.py`
- `_compute_qualified(company_props)`: returns `"Yes"` if `r__size_of_sales_team` > 0 **or** `total_funding` > $5,000,000 (strictly greater; exactly $5M is `"No"`), else `"No"`. Blank/None/unparseable values count as 0. Wired into `get_row_data()` as `computed["qualified"]`.

### Changed — `src/mapping.py` (`COLUMN_MAP`)
| Col | Before | After |
|---|---|---|
| AI | Closure Month | **Qualified** (computed) |
| AJ | Opportunity loss reason | Closure Month |
| AK | Opportunity loss reason deepdive | Opportunity loss reason |
| AL | SKIP — Trial loss reason (manual) | Opportunity loss reason deepdive |
| AM | SKIP — Intent signals (manual) | SKIP — Trial loss reason (manual) |
| AN | Deal Amount | SKIP — Intent signals (manual) |
| AO | `ae-kpi-tracker` dedup flag | **Deal Amount** |
| AP | — | `ae-kpi-tracker` dedup flag (`Is First?`) |

### Added — `scripts/insert_qualified_column.py`
- One-time script that inserts the blank column at AI in "Sales Pipeline 2026" and writes the `Qualified` header. **Run once only** — a second run inserts another blank column.

### Cross-project change
- `ae-kpi-tracker` `config.py`: `AMOUNT_COL` `"AN"` → `"AO"`, `DEDUP_COL` `"AO"` → `"AP"` (see its CHANGELOG). Verified 2026-10-05: the dedup column header `Is First?` sits at AP1 in the live sheet.

### Tests
- `TestComputeQualified` (7 tests), plus mapping tests for AI (Qualified), AJ (Closure Month) and AO (Deal Amount; renamed from the AN test). 77 tests passed.

---

## 2026-09-01 — Employee Count property fix + ICP size fallback

> ⚠️ **Superseded 2026-10-05:** the priority order described here (employee_count first) was wrong — see the entry above.

### Fixed — `src/mapping.py`
- `COMPANY_PROPERTIES`: Added `"employee_count"` (custom "R.Employee count" property, filled by the sales team). `"numberofemployees"` kept as fallback.
- Col I `COLUMN_MAP` entry: changed from `source="company"` / `prop="numberofemployees"` → `source="computed"` / `prop="employee_count_merged"`.

### Fixed — `src/hubspot.py`
- Added `_compute_employee_count_merged(company_props)` helper: returns `employee_count` if set, else `numberofemployees`, else `""`.
- Added `_compute_icp_size(employee_count, numberofemployees, sales_team)` helper: uses same fallback for Enterprise/Commercial/SMB/Startup buckets.
- `get_row_data()` now sets `computed["employee_count_merged"]` and delegates ICP size to `_compute_icp_size()`. Old inline ICP logic removed.
- Added `fmt_passthrough` to imports from `mapping`.

### Fixed — `tests/test_sync.py`
- Added `TestEmployeeCountMerge` (9 tests): merge priority, fallback, empty-string fallback, ICP size thresholds.
- Fixed pre-existing stale tests: `test_sdr_injected_into_af_column` → `test_sdr_injected_into_ag_column` (sync injects SDR into col AG, not AF).

### Root cause
Two HubSpot company properties exist for employee count:
- `employee_count` — custom "R.Employee count", manually filled by the sales team ✓
- `numberofemployees` — standard HubSpot auto-enriched field, often blank for developer-tool companies ✗ (was being read)

The code was reading `numberofemployees` (blank), ignoring `employee_count` (filled). ICP size had the same bug.

The "300 in Funding for sarvam.ai" issue is a HubSpot data entry error (`total_funding` field has 300 instead of actual funding). Correct that field directly in HubSpot.

**Verification:** 68/68 tests pass. Deployed to VM. Sync confirmed populating col I from `employee_count`.

---

## 2026-08-22 — Deal Amount → column AN

> ℹ️ **Moved 2026-09-29:** Deal Amount now lives in column **AO** (Qualified was inserted at AI) — see the 2026-09-29 entry.

### Added — `src/mapping.py`
- `"amount"` added to `DEAL_PROPERTIES` so HubSpot deal amount is fetched on every sync.
- Column AN entry added to `COLUMN_MAP`: header "Deal Amount", source `deal`, property `amount`, formatter `number`. Placed after AK (Opportunity loss reason deepdive); AL and AM are manual SKIP columns.

### Added — `tests/test_mapping.py`
- `test_amount_in_deal_properties` — verifies `amount` is in `DEAL_PROPERTIES`.
- `test_column_an_in_column_map` — verifies AN is mapped to `deal`/`amount`/`number`.

**Verification**
- Synced 339/344 rows; column AN populated in Sales Pipeline 2026 with deal amounts.
- Amounts visible in ae-kpi-tracker Weighted/Absolute Deal Value formulas (cols Q/R).

---

## 2026-08-21 — Still Active "Irrelevant" fix + Once-Daily Scheduler

### Fixed — `still_active` logic (`src/hubspot.py`)
- Stage label "Irrelevant" now maps to `Still active? = "No"` (was being treated as
  "Yes" because the match only checked "won", "lost", "converted").
- One-shot clear: `scripts/clear_columns.py` ran to wipe stale values from col AF
  (Real Opportunity?) and col AH (Source of meeting [Refined]) — both are `SKIP`
  columns that sync never writes; this was a manual one-time cleanup.

### Changed — Scheduler (`src/scheduler.py`)
- Consolidated from two jobs (04:30 UTC + 16:30 UTC) to one daily job at **08:00 UTC
  (1:30 PM IST)**, matching ae-kpi-tracker's daily sync cadence.
- Job ID renamed `sync_daily`; old IDs `sync_morning` / `sync_evening` removed.

**Verification**
- Deployed; VM log confirms `Scheduler started — job: sync_daily (08:00 UTC)`.
- Manual sync post-deploy: 339/344 rows synced, 0 errors,
  `next_run_at: 2026-08-21T08:00:00+00:00`.

---

## 2026-08-19 — Safe Deploy Script + Slack Fix + Sheet Chip Formatting

### Added — Safe deploy script (`deploy/deploy.sh`)
- Packs only `src/`, `templates/`, `static/`, `requirements.txt` — never `.env`.
- Root cause of two prior ERROR syncs: local `.env` was bundled in ad-hoc tar, overwriting VM's `GOOGLE_CREDENTIALS_FILE` path and `SCHEDULER_ENABLED=true`.
- `deploy/env-init.sh` added as a one-time helper for standing up the VM `.env` on a fresh instance.

### Fixed — Slack notifications (`src/slack.py`)
- Switched from `SLACK_WEBHOOK_URL` + `WebhookClient` (was returning 404) to `SLACK_BOT_TOKEN` + `SLACK_ALERT_CHANNEL` + `WebClient.chat_postMessage` via the Kaori bot.
- Channel: `D0B5WE9LGP4` (Kaori DM).

### Added — Sheet chip formatting (`scripts/setup_sheet.py`)
- Col C (Opportunity?): `Yes/No/Maybe` dropdown + green/red/yellow chip colours via conditional formatting.
- Col D (Owner?): 8-name dropdown (`strict: false`, `showCustomUi: true`) + per-owner chip colours via conditional formatting. Owner values now written as `"Chandra,Piyush"` (no space after comma) for compatibility with Google Sheets chip multi-select parsing.
- Conditional format priority: Closed Won full-row green > Opportunity? colours > Owner? colours.
- Script is idempotent — clears existing conditional format rules before applying.

### Open Issue — Owner? "Allow multiple selections"
- The Sheets API `setDataValidation` cannot enable the "Allow multiple selections" checkbox — that flag is Google Sheets UI-only and not exposed in the v4 API.
- Effect: clicking the Owner? dropdown shows a single-select picker; multi-owner values like `"Chandra,Piyush"` written by the sync display correctly but the picker doesn't show them as separately toggled chips.
- Workaround: manually tick "Allow multiple selections" in Google Sheets > Data > Data validation for col D, or accept current behaviour (values display and sync correctly; only the picker UX is limited).

---

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
