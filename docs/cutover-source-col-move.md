# Cutover runbook — move "Source of meeting (Muni Sheet)" from AG to E

**Planned: Friday 2026-10-23, window 08:00-09:00 IST (02:30-03:30 UTC), hard stop 09:30 IST.**
Plan: `~/.claude/plans/cheeky-roaming-hoare.md` (Release 2). Code is prepared on branch `feat/source-col-move` in
`hubspot-sheet-sync` and `ae-kpi-tracker`, and `feat/pipeline-csv-by-header` in the two analysis repos.
Nothing is deployed or moved until the steps below.

## What "cutover timing" means
The switch needs several things to change **together**: the column moves in the sheet, and two services (this one and
ae-kpi-tracker) must be redeployed to the new layout. The "cutover timing" is the slot where nobody and nothing else is
writing to the sheet while we do that. Slot = **08:00-09:00 IST**: after last night's 22:00 IST sync and the 06:30 IST
meeting-booked-done run (read-only on this sheet, ~14 min), before today's 10:00 IST sync (which then runs on the NEW
layout and doubles as the final proof), and long before ae-kpi's 13:30 IST job. Hard stop 09:30 IST.

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
Claude only acts while the session is open: **please start the session at ~07:55 IST** and say "start cutover".
| When | Step | Who | Gate |
|---|---|---|---|
| Thu 22 Oct, evening | Post the **heads-up** (message 1 below) | Rashu | - |
| Thu 22:00 IST (16:30 UTC) | First scheduled run on the new schedule/code | - | Claude checks Slack + `/api/status`: 0 errors, source line present. **If not clean, postpone.** |
| Fri 08:00 | Pre-flight: `--check` says PRE-move; repos clean on `main`; branches + `pre-col-move` tags exist; tests green; ssh works | Claude | all green |
| 08:00-08:10 | Baseline on the pre-move sheet: `verify_analysis_tabs.py` + `verify_weekly_tracker.py` (from ae-kpi `main`) | Claude | ALL OK |
| 08:10 | **GO / NO-GO #1** | Rashu | baseline OK |
| 08:10 | `move_source_column.py --apply --yes` (hidden backup tab, one `moveDimension`, cell-by-cell check) | Claude | "VERIFIED: all N rows match the backup" |
| 08:15 | Closed Won highlight reads `$U2`; analysis-tab formulas read `$U`/`$AB`; numbers equal baseline | Claude | match |
| 08:20 | **GO / NO-GO #2** (any failure -> `--rollback --yes`, stop) | Rashu | - |
| 08:20 | Merge the prep branches to `main` locally (hubspot-sheet-sync, ae-kpi-tracker) | Claude | tests green |
| 08:22 | Deploy hubspot-sheet-sync: `! cd ~/hubspot-sheet-sync && bash deploy/deploy.sh` | Rashu | service active |
| 08:26 | Deploy ae-kpi-tracker (tar/scp block in `ae-kpi-tracker.md` memory, `COPYFILE_DISABLE=1`) | Rashu | service active |
| 08:30 | `POST /api/setup`, then `POST /api/sync` on ae-kpi-tracker; both verify scripts (~10 min) | Claude | ALL OK, counts = baseline |
| 08:45 | Manual `POST /api/sync` on hubspot-sheet-sync (~6 min); spot-check 10 rows (E = source, F = Priority, U = Stage, AA = Conversion) | Claude | 0 errors, no "Header guard" line |
| 09:00 | **DONE**; push both repos + tags; update memory/CLAUDE.md | Claude | - |
| 10:00-10:07 | The scheduled sync runs on the new layout | - | Claude checks: 0 errors, source line, rows match; then Rashu posts message 2 |

**Hard stop 09:30 IST.** Anything unverified by then, or any VERIFY FAILED / mismatch -> `--rollback --yes`, redeploy the
`pre-col-move` tags (hubspot-sheet-sync `69b417a`, ae-kpi-tracker `3432a96`), `POST /api/setup` on ae-kpi. The hidden
backup tab stays until Rashu deletes it.
**Real deadlines if we slip:** hubspot-sheet-sync should be on the new layout before the 10:00 IST sync (if not, it just
refuses to write and reports "Header guard"; nothing is damaged). ae-kpi-tracker MUST match the sheet before its 13:30 IST
job (its old code has no guard).

## Don't
- Don't re-run `scripts/setup_sheet.py` (it deletes all conditional formats and resets the Owner chips).
- Don't deploy from a prep branch before the move; merge to `main` first (`deploy.sh` packs the checked-out tree).

## Before anyone else's tool breaks: ask
Our scan covered our own code only. Ask the team whether **anything else reads this tab by column letter** (another
Google Sheet via IMPORTRANGE, an Apps Script, Zapier/Make, a BI tool). Formulas inside the same spreadsheet, saved
filter views and pivot tables adjust automatically on a column move; things outside do not.

---
## Message 1 — heads-up (post Thursday evening; #sales-internal)
> :wrench: **Heads-up: a column is moving in "Sales Pipeline - 2026 - HubSpot Synced" tomorrow (Fri 23 Oct), 8:00-9:00 AM IST**
>
> We're moving **Source of meeting (Muni Sheet)** from column AG to **column E**, right after Owner?. Everything that was in E-AF shifts one column to the right (Priority is now F, ICP Segment G, ICP Size H, Stage U, Conversion AA, Still active? AB). Columns A-D and AH onward don't move.
>
> **What you need to do:** please don't edit the sheet between **8:00 and 9:30 AM IST** tomorrow. Filters, formatting, dropdowns and the KPI tabs move with the column automatically.
>
> **One ask:** if you have your **own sheet, script or tool** that reads this tab by column letter (IMPORTRANGE, Apps Script, Zapier, a BI tool), reply here today so we can fix it with the move.
>
> Source of meeting is also now filled from the Meeting Booked vs Done analysis (431 of 455 rows). The sheet now syncs from HubSpot twice a day, at **10 AM and 10 PM IST**.

## Message 2 — done (post after the 10:00 AM IST sync has been checked, ~10:10 AM)
> :white_check_mark: **The column move is done.** *Source of meeting (Muni Sheet)* is now **column E**; everything that was in E-AF is one column to the right (Priority F, ICP Segment G, ICP Size H, Stage U, Conversion AA, Still active? AB). KPI tabs and the Weekly tracker were re-checked against the old numbers and match, and this morning's 10 AM sync ran fine on the new layout. If anything looks off, reply here.
