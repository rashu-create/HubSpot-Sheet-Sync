# Cutover runbook — move "Source of meeting (Muni Sheet)" from AG to E

**Planned: Friday 2026-10-23, right after the 10:00 AM IST sync (04:30 UTC).**
Plan: `~/.claude/plans/cheeky-roaming-hoare.md` (Release 2). Code is prepared on branch `feat/source-col-move` in
`hubspot-sheet-sync` and `ae-kpi-tracker`, and `feat/pipeline-csv-by-header` in the two analysis repos.
Nothing is deployed or moved until the steps below.

## What "cutover timing" means
The switch needs several things to change **together**: the column moves in the sheet, and two services (this one and
ae-kpi-tracker) must be redeployed to the new layout. The "cutover timing" is the slot where nobody and nothing else is
writing to the sheet while we do that. Slot = after the 10:00 IST sync finishes (it writes the sheet), before ae-kpi's
13:30 IST job (it rewrites formulas), and while Rashu is at the keyboard. That is **10:10-11:15 IST, hard stop 12:30 IST**.

## Why one coordinated switch
- `ae-kpi-tracker` reads the tab by column letter; today's deployed code has no guard, so after the move it would
  silently give wrong numbers at its 13:30 IST run -> it must be redeployed straight after the move.
- `hubspot-sheet-sync` is protected in both directions (header guard): whichever version is out of step refuses to write
  and reports "Header guard: ...".

## Who does what
| Who | What |
|---|---|
| **Claude** | read-only checks, baseline, the sheet move script, verification, merging branches locally, `POST /api/setup|sync` via ssh/curl |
| **Rashu** | posts the heads-up, makes the go/no-go calls, runs the two deploys with `!` (the classifier blocks Claude's remote writes) |

If the classifier also blocks `move_source_column.py --apply`, Rashu runs that one line with `!` too.

## Timeline (IST / UTC)
| When | Step | Who | Gate |
|---|---|---|---|
| Thu 22 Oct, evening | Post the **heads-up** (message 1 below) | Rashu | - |
| Thu 22:00 IST (16:30 UTC) | First scheduled run on the new schedule/code | - | Claude checks Slack + `/api/status`: 0 errors, source line present. **If not clean, postpone.** |
| Fri 09:45 IST | Pre-flight: `--check` says PRE-move; both repos clean on `main`; branches exist; tests green; `gcloud` login works | Claude | all green |
| Fri 10:00-10:07 IST | The scheduled sync runs (about 5-6 min) | - | status `running:false`, 0 errors |
| 10:10 | Baseline on the pre-move sheet: `verify_analysis_tabs.py` + `verify_weekly_tracker.py` (from ae-kpi `main`) | Claude | ALL OK |
| 10:20 | **GO / NO-GO #1** | Rashu | baseline OK, no one editing |
| 10:20 | `move_source_column.py --apply --yes` (hidden backup tab, one `moveDimension`, cell-by-cell check) | Claude | "VERIFIED: all N rows match the backup" |
| 10:25 | Check Closed Won highlight formula reads `$U2`; analysis-tab formulas now read `$U`/`$AB`; numbers equal baseline | Claude | match |
| 10:30 | **GO / NO-GO #2** (any failure -> `--rollback --yes`, stop) | Rashu | - |
| 10:30 | Merge the prep branches to `main` locally (hubspot-sheet-sync, ae-kpi-tracker) | Claude | tests green |
| 10:32 | Deploy hubspot-sheet-sync: `! cd ~/hubspot-sheet-sync && bash deploy/deploy.sh` | Rashu | service active |
| 10:36 | Deploy ae-kpi-tracker (tar/scp block in `ae-kpi-tracker.md` memory, `COPYFILE_DISABLE=1`) | Rashu | service active |
| 10:40 | `POST /api/setup`, then `POST /api/sync` on ae-kpi-tracker; then both verify scripts (about 8 min) | Claude | ALL OK, counts = baseline |
| 10:55 | Manual `POST /api/sync` on hubspot-sheet-sync (about 6 min); spot-check 10 rows (E = source, F = Priority, U = Stage, AA = Conversion) | Claude | 0 errors, no "Header guard" line |
| 11:15 | **DONE**; push both repos + tags; update memory/CLAUDE.md; post message 2 | Claude / Rashu | - |

Safety margin: ae-kpi's own job is 13:30 IST; hubspot-sheet-sync's next scheduled run is 22:00 IST.
**Rollback trigger:** anything unverified by 12:30 IST, or any VERIFY FAILED / mismatch -> `--rollback --yes`, redeploy
the `pre-col-move` tags (hubspot-sheet-sync `69b417a`, ae-kpi-tracker `3432a96`), `POST /api/setup` on ae-kpi. The hidden
backup tab stays until Rashu deletes it.

## Don't
- Don't re-run `scripts/setup_sheet.py` (it deletes all conditional formats and resets the Owner chips).
- Don't deploy from a prep branch before the move; merge to `main` first (`deploy.sh` packs the checked-out tree).

## Before anyone else's tool breaks: ask
Our scan covered our own code only. Ask the team whether **anything else reads this tab by column letter** (another
Google Sheet via IMPORTRANGE, an Apps Script, Zapier/Make, a BI tool). Formulas inside the same spreadsheet, saved
filter views and pivot tables adjust automatically on a column move; things outside do not.

---
## Message 1 — heads-up (post Thursday evening; #sales-internal)
> :wrench: **Heads-up: a column is moving in "Sales Pipeline - 2026 - HubSpot Synced" tomorrow (Fri 23 Oct), ~10:10-11:15 AM IST**
>
> We're moving **Source of meeting (Muni Sheet)** from column AG to **column E**, right after Owner?. Everything that was in E-AF shifts one column to the right (Priority is now F, ICP Segment G, ICP Size H, Stage U, Conversion AA, Still active? AB). Columns A-D and AH onward don't move.
>
> **What you need to do:** please don't edit the sheet between **10:00 and 11:15 AM IST** tomorrow. Filters, formatting, dropdowns and the KPI tabs move with the column automatically.
>
> **One ask:** if you have your **own sheet, script or tool** that reads this tab by column letter (IMPORTRANGE, Apps Script, Zapier, a BI tool), reply here today so we can fix it with the move.
>
> Source of meeting is also now filled from the Meeting Booked vs Done analysis (431 of 455 rows). The sheet now syncs from HubSpot twice a day, at **10 AM and 10 PM IST**.

## Message 2 — done (post after ~11:15 AM IST)
> :white_check_mark: **The column move is done.** *Source of meeting (Muni Sheet)* is now **column E**; everything that was in E-AF is one column to the right (Priority F, ICP Segment G, ICP Size H, Stage U, Conversion AA, Still active? AB). KPI tabs and the Weekly tracker were re-checked against the old numbers and match. You can edit the sheet again. If anything looks off, reply here.
