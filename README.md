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

Defined in `src/mapping.py` (`COLUMN_MAP`). **SKIP** columns are manual and never written.
A = Domain (input, never written) · B Date of First Meeting (SKIP) · C Opportunity? · D Owner? ·
E Priority · F ICP Segment · G ICP Size · H Funding · I Employee Count · J Size of Sales Team ·
K Job Title of Champion · L–N L1/L2/L3 Qualified · O–Q L1/L2/L3 Comments · R Next Steps ·
S Due Date · T Stage · U Trial Stage · V/W Trial Start/End · X Notes from Call · Y SKIP ·
Z Conversion · AA Still active? · AB Trial done? · AC–AF SKIP · AG Source of meeting (Muni
Sheet) · AH SKIP · AI Qualified · AJ Closure Month · AK/AL Opportunity loss reason (+ deepdive) ·
AM/AN SKIP · AO Deal Amount · AP "Domain key" (owned by ae-kpi-tracker).

## Source of meeting (col AG)

Taken from the Meeting Booked vs Done analysis (`src/meeting_source.py`): the meeting that matches the row's
Date of First Meeting, else the nearest earlier one, else the earliest; the old Weekly Meetings sheet is the
fallback. Never blanks a cell. Preview any change with the read-only `FORCE_IPV4=1 python scripts/source_diff.py`.

## Header guard

Every write first checks row 1 against `COLUMN_MAP`; if a column was inserted, moved or renamed nothing is written
and the run reports `Header guard: …`. Fix the sheet (or `COLUMN_MAP`) and re-run.

## Who else reads this sheet — check before changing columns

Moving or inserting a column breaks anything that addresses columns by letter or position.

| Consumer | How it addresses columns | Deployed |
|---|---|---|
| `~/ae-kpi-tracker` | Hardcoded letters in formulas and constants (B, C, D, F, G, I, J, T, Z, AA, AO, AP) | VM, port 8010 |
| `~/meeting-booked-done-analysis` `src/ingest.py` | Header names | VM timer |
| `~/meeting-booked-done-analysis` and `~/sales-pipeline-analysis` `src/pipeline_csv.py` | Fixed CSV indices (offline export) | local |
| `~/slack-bot` `pipeline_sheet_api.py` | Column A only | VM |
| `~/weekly-sales-tracker` | A different sheet (the manager's own) | — |

Previous column change (2026-09-29, Qualified inserted at AI) was coordinated across
hubspot-sheet-sync and ae-kpi-tracker; see `docs/superpowers/plans/2026-09-29-qualified-column.md`.

## Gotchas

- Use HubSpot stage **labels**, never ids (`closedwon` is *Pricing Discussion* in this portal).
- The Owner? multi-select chips must be re-ticked by hand in Sheets after `scripts/setup_sheet.py`
  (the API cannot set that flag).
- Forecast tabs are wiped and rewritten every run; manual edits there are lost.
- `scripts/archive/` holds finished one-shot scripts — do not re-run them.
