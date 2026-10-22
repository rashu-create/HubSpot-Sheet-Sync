# Cutover runbook — move "Source of meeting (Muni Sheet)" from AG to E

Plan: `~/.claude/plans/cheeky-roaming-hoare.md` (Release 2). Everything below is **read-only until step 5**.
Code is prepared on branch `feat/source-col-move` in `hubspot-sheet-sync` and `ae-kpi-tracker`
(`feat/pipeline-csv-by-header` in the two analysis repos). **Nothing is deployed or moved yet.**

## Why it must be one coordinated cutover
- `ae-kpi-tracker` reads the tab by column letter; its 08:00 UTC job rewrites the Weekly tracker lookups every day.
  Old code after the move = silently wrong numbers; new code before the move = its header guard refuses and reports.
- `hubspot-sheet-sync` is protected in both directions: the deployed code (header guard, `69b417a`) expects the OLD
  layout and the branch code expects the NEW one, so whichever is out of step just refuses to write and reports
  "Header guard: …" in Slack. Only `ae-kpi-tracker`'s currently deployed code has no guard, so it is the one that must
  be redeployed straight after the move (before its 08:00 UTC job).

## Window
Right after the 04:30 UTC sync finishes (~04:36 UTC = 10:06 IST), ending before ae-kpi's 08:00 UTC job.
The next hubspot-sheet-sync run is 16:30 UTC, so there is no race with the sync either.

## Before (any time earlier)
1. `ae-kpi-tracker`: confirm what the VM runs vs `main` (diff the files), tag the deployed commit `pre-col-move`.
   `hubspot-sheet-sync`: tag the deployed commit `pre-col-move` (currently `69b417a`).
2. Baseline: `ae-kpi-tracker` -> `python scripts/verify_analysis_tabs.py` and `python scripts/verify_weekly_tracker.py`
   must report ALL OK; export the pipeline tab + analysis tabs to CSV (`scripts/` or File > Download).
3. `FORCE_IPV4=1 python scripts/move_source_column.py` -> must say "PRE-move".
4. Rashu tells managers/AEs a short heads-up (draft: "Source of meeting is moving to column E; everything between
   E and AG shifts one column right. Please don't edit the sheet 10:00-10:30 IST.").

## Cutover (in this order)
5. `FORCE_IPV4=1 python scripts/move_source_column.py --apply --yes` (hubspot-sheet-sync repo).
   It makes a hidden backup tab, moves the column, verifies every cell against the backup and prints the conditional-format
   formulas. **Stop if it says VERIFY FAILED** -> `--rollback --yes`.
6. Check the printed conditional-format formulas: the Closed Won rule must read `=$U2="Closed Won"`.
   If it still says `$T2`, fix that one rule in the sheet (do NOT re-run `setup_sheet.py`: it resets the Owner chips).
7. Check in the sheet that the analysis tabs' formulas now read the shifted columns (e.g. a By-ICP tab formula shows
   `$AB$2:$AB` / `$U$2:$U`) and that numbers equal the baseline (Sheets rewrites references on a column move).
8. Deploy `hubspot-sheet-sync` (`bash deploy/deploy.sh` from branch `feat/source-col-move` after merging to `main`).
9. Deploy `ae-kpi-tracker`, then `POST /api/setup`, then `POST /api/sync`.
10. `python scripts/verify_analysis_tabs.py`, `python scripts/verify_weekly_tracker.py` -> ALL OK, counts equal baseline.
11. Manual `POST /api/sync` on hubspot-sheet-sync -> 0 errors, no "Header guard" line; spot-check 10 rows (E = source,
    F = Priority, U = Stage, AA = Conversion).
12. Merge the branches to `main`, push, update memory/CLAUDE.md. Send the managers' note.

## Rollback
`FORCE_IPV4=1 python scripts/move_source_column.py --rollback --yes`, redeploy the `pre-col-move` tags of both services,
`POST /api/setup` on ae-kpi-tracker. The hidden backup tab stays until Rashu deletes it.
