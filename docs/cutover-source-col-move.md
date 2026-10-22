# Record: "Source of meeting" moved from AG to column E (executed 2026-10-22 21:40 IST)

Status: **DONE** (the planned 08:00 IST window was dropped; Rashu asked for it immediately). Kept as the record of what was
done, how to roll back, and the lessons. Next time a column moves, start here.

## What was done
1. Pre-flight (read-only): `scripts/move_source_column.py` said PRE-move; both services idle.
2. Baseline: `scripts/snapshot_values.py --out /tmp/cutover_baseline.json` (46 other tabs).
3. Move: `scripts/move_source_column.py --apply --yes` -> hidden backup tab `Sales Pipeline 2026 – backup 2026-10-22`, ONE
   `moveDimension` AG -> E, all 456 rows verified against the backup. The Closed Won conditional format moved to `$U2` by itself.
4. `snapshot_values.py --compare` -> IDENTICAL (Sheets rewrites cross-tab references on a column move).
5. Deploys: VM `.env` (Next Steps settings), `bash deploy/deploy.sh` (hubspot-sheet-sync), tar/scp (ae-kpi-tracker).
   Both new versions carry a header guard (row 1 vs the expected headers), so deploy order was safe in either direction.
6. Verification: ae-kpi `verify_analysis_tabs.py` (1,744 cells) and `verify_weekly_tracker.py` (5,516 checks) ALL OK; first sync on
   the new layout 446/455 rows, 0 errors, source filled 431, every column holds the right kind of data.

## Lessons
- **Do NOT run ae-kpi `POST /api/setup` after a pure column move.** It is not needed (Sheets adjusts the formulas) and it resets the
  typed filter inputs (From/Till dates on every analysis tab, the Dashboard filter). It did, 19 cells; restored from the snapshot.
- Compare a full-tab value snapshot before/after (`snapshot_values.py`): letter-independent, caught the setup side effect.
- Rehearse on a throwaway duplicate of the tab first (`PIPELINE_TAB_NAME="ZZ rehearsal"`): apply, check, rollback all worked.
- Mind the 60 reads/minute Sheets quota (one service account shared by the sync, ae-kpi and these scripts).

## Rollback (while the backup tab exists)
`FORCE_IPV4=1 python scripts/move_source_column.py --rollback --yes`, then redeploy the `pre-col-move` tags
(hubspot-sheet-sync `69b417a`, ae-kpi-tracker `3432a96`). Do not run `/api/setup`.

## Message to the AEs (draft, not sent) — Next Steps / Due Date two-way sync + schema change
> :arrows_counterclockwise: **You can now update *Next Steps* and *Due Date* in the pipeline sheet, and it updates HubSpot** (from Mon 26 Oct)
>
> Edit **Next Steps (column S)** and **Due Date (column T)** in *Sales Pipeline - 2026 - HubSpot Synced*. Your edits are pushed to the HubSpot deal at the **10 AM and 10 PM IST** syncs, so there's no need to update both places.
>
> **A few rules:**
> • A field can't be left empty. If you clear it, it's restored from HubSpot.
> • Due Date must be a real date, from 30 days back to 1 year ahead.
> • If the same field was also changed in HubSpot since the last sync, **HubSpot wins** and your sheet edit is skipped.
> • It only works on rows that match a HubSpot deal. A few rows where the same company appears twice are skipped.
>
> If an edit doesn't show up in HubSpot after the next sync, tell me.
>
> :wrench: *Schema change:* **Source of meeting** moved to **column E**, and everything between E and AG shifted one column right (Priority is now F, ICP Segment G, ICP Size H, Stage U, Conversion AA, Still active? AB).
