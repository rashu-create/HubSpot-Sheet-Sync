"""Next Steps / Due Date write-back, per sync run (see src/next_steps.py for the rules).

NEXT_STEPS_WRITEBACK = off | dry_run | on   (default off)
  off      nothing changes.
  dry_run  decisions are made and logged (WOULD_UPDATE) but nothing is pushed to HubSpot; the AE's cell is left as
           typed, so a dry run never destroys an edit.
  on       valid edits are pushed to the HubSpot deal.

run_sync() calls prepare() once, add_row() for every row, before_write() just before the sheet write (race guard +
push) and after_write() (snapshot + log). Anything unexpected switches the feature off for that run and is reported in
`errors`; it never stops the normal sync.
"""
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from src import hubspot, next_steps_log, next_steps_state, sheets
from src.header_guard import check_headers
from src.mapping import DUE_DATE_COL, NEXT_STEPS_COL, normalize_domain
from src.next_steps import (
    BASELINE, BLOCKED_EMPTY, BLOCKED_INVALID_DATE, CONFLICT, DUE_DATE, NEXT_STEPS, SKIPPED_DEAL_CHANGED,
    SKIPPED_DUPLICATE_ROW, SKIPPED_NO_DEAL, SKIPPED_RACE, UPDATE, RowCtx, norm_date, norm_text, plan_row,
)

logger = logging.getLogger(__name__)

MODES = ("off", "dry_run", "on")
_RISKY_TEXT = re.compile(r"^[=+\-@]|^[\d\s.,/:\-]+$")     # Sheets would turn these into formulas / numbers / dates


def safe_cell_text(value: str) -> str:
    """Leading apostrophe keeps USER_ENTERED from reinterpreting text (it is not stored in the cell)."""
    return "'" + value if isinstance(value, str) and value and _RISKY_TEXT.match(value) else value


class NextStepsSync:
    def __init__(self, mode: str = "off", sync_dry_run: bool = False, state_path: Path | str | None = None,
                 today: date | None = None):
        self.errors: list[str] = []
        if mode not in MODES:
            self.errors.append(f"Next steps: unknown NEXT_STEPS_WRITEBACK {mode!r} (use off, dry_run or on) — treated as off")
            mode = "off"
        self.mode = mode
        self.sync_dry_run = sync_dry_run
        self.state_path = Path(state_path) if state_path else next_steps_state.DEFAULT_PATH
        self.today = today or datetime.now(timezone.utc).date()
        self.active = False
        self.plans: list = []
        self.cells: dict[int, tuple] = {}
        self.snapshot: dict = {}
        self.dups: set[str] = set()
        self.push_results: dict[str, tuple[bool, str]] = {}
        self._started = time.monotonic()
        self._run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")

    @classmethod
    def from_env(cls, sync_dry_run: bool = False) -> "NextStepsSync":
        return cls(
            mode=os.getenv("NEXT_STEPS_WRITEBACK", "off").strip().lower() or "off",
            sync_dry_run=sync_dry_run,
            state_path=os.getenv("NEXT_STEPS_STATE_FILE", "").strip() or None,
        )

    # ── before the row loop ──────────────────────────────────────────────────

    def prepare(self, domain_rows: list[tuple[int, str]]) -> None:
        """Layout check + read the cells and the snapshot. On any problem the feature is off for this run."""
        if self.mode == "off":
            return
        try:
            problems = check_headers(sheets.read_pipeline_header_row(), [NEXT_STEPS_COL, DUE_DATE_COL])
            if problems:
                self.errors.append("Next steps: pipeline layout changed, write-back is off for this run — "
                                   + "; ".join(problems))
                return
            self.cells = sheets.read_next_steps_cells()
            self.snapshot = next_steps_state.load(self.state_path)
        except Exception as exc:
            logger.error("Next steps prepare failed: %s", exc, exc_info=True)
            self.errors.append(f"Next steps: could not read the sheet — {exc}")
            return
        seen: set[str] = set()
        for _row, domain in domain_rows:
            key = normalize_domain(domain)
            (self.dups if key in seen else seen).add(key)
        self.active = True

    # ── per row ──────────────────────────────────────────────────────────────

    def add_row(self, row: int, domain: str, meta: dict, row_data: dict | None) -> None:
        """Plan one row. Removes the S/T cells from row_data when the AE's own value must stay in the sheet."""
        if not self.active:
            return
        key = normalize_domain(domain)
        sheet_s, sheet_t = self.cells.get(row, ("", ""))
        if row_data is None:
            ctx = RowCtx(row, key, "", "", "", sheet_s, sheet_t, "", "")
        else:
            ctx = RowCtx(row, key, meta.get("_deal_id", ""), meta.get("_deal_name", ""), row_data.get("D", ""),
                         sheet_s, sheet_t, meta.get("_hs_s", ""), meta.get("_hs_t", ""))
        plan = plan_row(ctx, self.snapshot.get(key), self.today, duplicate=key in self.dups)
        self.plans.append(plan)
        if row_data is None:
            return
        for name, col in ((NEXT_STEPS, NEXT_STEPS_COL), (DUE_DATE, DUE_DATE_COL)):
            if name in plan.keep:
                row_data.pop(col, None)
        if NEXT_STEPS_COL in row_data:
            row_data[NEXT_STEPS_COL] = safe_cell_text(row_data[NEXT_STEPS_COL])

    # ── just before / after the sheet write ──────────────────────────────────

    def before_write(self, updates: list[dict]) -> None:
        """Race guard (drop rows an AE touched during the run), then push the valid edits."""
        if not self.active or self.sync_dry_run:
            return
        try:
            now = sheets.read_next_steps_cells()
        except Exception as exc:
            self.errors.append(f"Next steps: could not re-check the sheet, nothing pushed this run — {exc}")
            for plan in self.plans:
                plan.push.clear()
            return

        by_row = {u["row"]: u for u in updates}
        for plan in self.plans:
            if plan.skip or now.get(plan.ctx.row) == self.cells.get(plan.ctx.row):
                continue
            plan.skip, plan.push, plan.keep = SKIPPED_RACE, {}, set()
            if plan.ctx.row in by_row:
                for col in (NEXT_STEPS_COL, DUE_DATE_COL):
                    by_row[plan.ctx.row]["values"].pop(col, None)

        pushes = {p.ctx.deal_id: p.push for p in self.plans if p.push}
        if self.mode == "on" and pushes:
            self.push_results = hubspot.push_deal_updates(pushes)
            failed = {d: r for d, (ok, r) in self.push_results.items() if not ok}
            if failed:
                self.errors.append(f"Next steps: push failed for {len(failed)} deal(s) — first: "
                                   f"{next(iter(failed.values()))}")

    def after_write(self) -> None:
        """Rebuild the snapshot from what the sheet holds now, save it, and append the decision log."""
        if not self.active or self.sync_dry_run:
            return
        old = self.snapshot
        try:
            after = sheets.read_next_steps_cells()
        except Exception as exc:
            self.errors.append(f"Next steps: could not re-read the sheet, snapshot not updated — {exc}")
            after = None

        if after is not None:
            new = dict(old)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for plan in self.plans:
                if plan.skip in (SKIPPED_NO_DEAL, SKIPPED_DUPLICATE_ROW, SKIPPED_RACE) or not plan.ctx.deal_id:
                    continue
                key, cell_s, cell_t = plan.ctx.domain, *after.get(plan.ctx.row, ("", ""))
                prev = old.get(key) if (old.get(key) or {}).get("deal_id") == plan.ctx.deal_id else None
                entry = {"deal_id": plan.ctx.deal_id, "synced_at": stamp}
                decisions = {d.field: d for d in plan.decisions}
                for name, cell, hs in ((NEXT_STEPS, cell_s, plan.ctx.hs_s), (DUE_DATE, cell_t, plan.ctx.hs_t)):
                    d = decisions.get(name)
                    pushed_ok = self.mode == "on" and self.push_results.get(plan.ctx.deal_id, (False, ""))[0]
                    if d is not None and d.action == UPDATE and not pushed_ok:
                        entry[name] = (prev or {}).get(name, "")            # dry run / failed: edit still pending
                        continue
                    value = norm_date(cell) if name == DUE_DATE else norm_text(cell)
                    entry[name] = value if value is not None else (norm_date(hs) or "")
                new[key] = entry
            try:
                next_steps_state.save(new, self.state_path)
            except Exception as exc:
                self.errors.append(f"Next steps: could not save the snapshot — {exc}")

        try:
            rows = next_steps_log.build_rows(
                self._run_id, self.mode, self.plans, self.push_results, datetime.now(timezone.utc),
                duration_s=time.monotonic() - self._started, snapshot=old)
            next_steps_log.append(rows)
        except Exception as exc:
            logger.error("Next steps log write failed: %s", exc, exc_info=True)
            self.errors.append(f"Next steps: log write failed — {exc}")

    # ── numbers for the Slack line / run history ─────────────────────────────

    def summary(self) -> dict:
        s = dict(updated=0, would_update=0, conflicts=0, blocked=0, errors=0)
        for plan in self.plans:
            for d in plan.decisions:
                if d.action == UPDATE:
                    if self.mode == "dry_run":
                        s["would_update"] += 1
                    elif self.push_results.get(plan.ctx.deal_id, (False, ""))[0]:
                        s["updated"] += 1
                elif d.action == CONFLICT:
                    s["conflicts"] += 1
                elif d.action in (BLOCKED_EMPTY, BLOCKED_INVALID_DATE):
                    s["blocked"] += 1
        s["errors"] = sum(1 for ok, _ in self.push_results.values() if not ok)
        return s
