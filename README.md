# HubSpot-Sheet-Sync

One-way sync from the HubSpot **Sales Pipeline** into the **"Sales Pipeline 2026"** tab of the
Google Sheet *"Sales Pipeline - 2026 - HubSpot Synced"*. Column A (domain) is the only input;
every other synced column is overwritten from HubSpot on each run. The service also rebuilds
the `Sales Forecast - Overview` and `Sales Forecast - AEs` tabs, and posts the result to Slack
(Kaori bot).

- **Live at:** `https://hubspot-sheet-sync.data.reo.dev` (GCP VM `sales-production`, port 8008,
  `/opt/services/hubspot-sheet-sync`)
- **Stack:** Python, FastAPI, APScheduler, httpx, gspread, slack-sdk, Jinja2
- **Schedule:** twice a day at **10:00 and 22:00 IST** (04:30 and 16:30 UTC), in-process APScheduler (`src/scheduler.py`, `SCHEDULER_ENABLED=true`)
- **History:** `CHANGELOG.md`

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in secrets — never commit .env
uvicorn src.api:app --port 8008
pytest -q                   # all tests are offline (HubSpot/Sheets are stubbed in tests/conftest.py)
```

On this Mac, IPv6 to Google hangs — run any script that touches Sheets with IPv4 forced.

Endpoints: `GET /health`, `GET /` (dashboard), `POST /api/sync` (manual sync, background thread),
`GET /api/status`, `GET /api/logs`.

## Deploy

```bash
bash deploy/deploy.sh      # packs src/ templates/ static/ requirements.txt (never .env) → scp → restart
```

First-time VM env setup: `bash deploy/env-init.sh`. SSH, nginx/Consul and logs are in the
shared VM notes (`vm-ops.md`). `scripts/` is not deployed.

## Environment

See `.env.example`. Keys: `HUBSPOT_API_TOKEN`, `HUBSPOT_AE_EMAILS`, `GOOGLE_CREDENTIALS_FILE`,
`PIPELINE_SHEET_ID`, `PIPELINE_TAB_NAME`, `SOURCE_SHEET_ID`, `SLACK_BOT_TOKEN`,
`SLACK_ALERT_CHANNEL`, `PORT`, `LOG_LEVEL`, `SCHEDULER_ENABLED`, `AUTH_ENABLED`, `ALLOWED_EMAILS`,
`ANALYSIS_SHEET_ID` (Meeting Booked vs Done analysis sheet — source of the Source-of-meeting column).

## How a sync works (`src/sync.py`)

1. Load Source-of-meeting inputs (analysis `Data` tab + the old Weekly Meetings sheet as fallback). 2. Read the domains in column A. 3. Pre-fetch all
Sales Pipeline deals from HubSpot. 4. Look up each domain (company domain → company name →
deal name → HubSpot company search). 5. Write the rows to the sheet. 6. Rebuild the forecast
tabs. A forecast failure is recorded as `Forecast: …` and never stops the row sync.

## Column map

Defined in `src/mapping.py` (`COLUMN_MAP`; `SOURCE_COL`). **SKIP** columns are manual and never written.
Layout since the 2026-10-22 move ("Source of meeting" went to E, the old E..AF shifted right by one):
A = Domain (input, never written) · B Date of First Meeting (SKIP) · C Opportunity? · D Owner? ·
**E Source of meeting (Muni Sheet)** · F Priority · G ICP Segment · H ICP Size · I Funding · J Employee Count ·
K Size of Sales Team · L Job Title of Champion · M–O L1/L2/L3 Qualified · P–R L1/L2/L3 Comments · S Next Steps ·
T Due Date · U Stage · V Trial Stage · W/X Trial Start/End · Y Notes from Call · Z SKIP (Source of meeting (First HS)) ·
AA Conversion · AB Still active? · AC Trial done? · AD–AF SKIP (Closed Won/Lost Reason) · AG SKIP (Real Opportunity?) ·
AH SKIP (Source of meeting [Refined]) · AI Qualified · AJ Closure Month · AK/AL Opportunity loss reason (+ deepdive) ·
AM/AN SKIP · AO Deal Value · AP "Domain key" (owned by ae-kpi-tracker).

## Source of meeting (col E; col AG before the move)

Taken from the Meeting Booked vs Done analysis (`src/meeting_source.py`): the meeting that matches the row's
Date of First Meeting, else the nearest earlier one, else the earliest; the old Weekly Meetings sheet is the
fallback. Never blanks a cell. Preview any change with the read-only `FORCE_IPV4=1 python scripts/source_diff.py`.

## Next Steps / Due Date two-way sync (S / T → HubSpot)

AEs can edit **Next Steps** (col S) and **Due Date** (col T) in the sheet; valid edits are pushed to the HubSpot deal
(`next_steps_management`, `next_steps_due_date_management` — the same properties the sheet reads first, and the ones
slack-bot's Next Steps Alerts use). Runs inside every sync (10 AM / 10 PM IST). `NEXT_STEPS_WRITEBACK` = `off`
(default) | `dry_run` (decisions logged as WOULD_UPDATE, nothing sent, AE edits kept) | `on`. With `dry_run`,
`NEXT_STEPS_LIVE_FROM=YYYY-MM-DD` (IST date) switches it to `on` by itself from that date (set to 2026-10-26 in
production); `off` is the kill switch (edit the VM `.env` and restart).
- **How it tells an AE edit from a HubSpot edit:** a snapshot (`data/next_steps_state.json`, on the VM only) of the values
  the sync last left in each cell. Sheet != snapshot = AE edit. The HubSpot side uses the same deal -> company fallback
  as the cell, so they compare like for like. First sight of a row only records a baseline (never pushes).
- **Rules:** HubSpot wins a conflict (both changed) and it is logged; a cleared cell is restored, never pushed; a Due Date
  must be a real date between 30 days ago and 1 year ahead; rows without a matched deal or with a duplicate domain are
  skipped; a cell edited *while a run is in flight* is left alone until the next run.
- **Log:** every decision goes to the tab `HubSpot Next Steps Sync Logs` of the Kaori Logs sheet
  (`NEXT_STEPS_LOG_SHEET_ID`): UPDATED / WOULD_UPDATE / CONFLICT_HUBSPOT_WINS / BLOCKED_EMPTY / BLOCKED_INVALID_DATE /
  ERROR / SKIPPED_* plus one SUMMARY row per run. Slack gets one summary line.
- **Sheet guardrails:** `scripts/setup_next_steps_guardrails.py --apply` adds a "valid date" rule on Due Date and header notes
  (never touches conditional formats).
- Code: `src/next_steps.py` (rules, pure), `src/next_steps_sync.py` (per-run orchestration), `src/next_steps_log.py`,
  `src/next_steps_state.py`, `hubspot.push_deal_updates`.

## Header guard

Every write first checks row 1 against `COLUMN_MAP`; if a column was inserted, moved or renamed nothing is written
and the run reports `Header guard: …`. Fix the sheet (or `COLUMN_MAP`) and re-run.

## Who else reads this sheet — check before changing columns

Moving or inserting a column breaks anything that addresses columns by letter or position. Cutover runbook for the
Source-of-meeting move: `docs/cutover-source-col-move.md`.

| Consumer | How it addresses columns | Deployed |
|---|---|---|
| `~/ae-kpi-tracker` | Named constants `COL_*` in `src/config.py` (+ header guard); formulas read them | VM, port 8010 |
| `~/meeting-booked-done-analysis` `src/ingest.py` | Header names | VM timer |
| `~/meeting-booked-done-analysis` and `~/sales-pipeline-analysis` `src/pipeline_csv.py` | Header names (offline export; was fixed indices until 2026-10-22) | local |
| `~/slack-bot` `pipeline_sheet_api.py` | Column A only | VM |
| `~/weekly-sales-tracker` | A different sheet (the manager's own) | — |

**Tabs in this sheet written by another service** (this service never touches them; do not rename or move their columns):

| Tab | Written by | When |
|---|---|---|
| `Meetings Booked` | `~/meeting-booked-done-analysis` `meetings_booked_tab.py` (VM timer `meetings-booked-tab`) — every 2026 booked meeting + whether it happened. Rebuilt weekly; the only hand-typed column is `Link to Slack` (kept by Key). Has its own header guard. | Mon 08:00 IST |

Previous column change (2026-09-29, Qualified inserted at AI) was coordinated across
hubspot-sheet-sync and ae-kpi-tracker; see `docs/superpowers/plans/2026-09-29-qualified-column.md`.

## Gotchas

- Use HubSpot stage **labels**, never ids (`closedwon` is *Pricing Discussion* in this portal).
- The Owner? multi-select chips must be re-ticked by hand in Sheets after `scripts/setup_sheet.py`
  (the API cannot set that flag).
- Forecast tabs are wiped and rewritten every run; manual edits there are lost.
- `scripts/archive/` holds finished one-shot scripts — do not re-run them.
