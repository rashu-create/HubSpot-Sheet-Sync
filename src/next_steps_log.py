"""Decision log for the Next Steps / Due Date write-back: tab "HubSpot Next Steps Sync Logs" of the Kaori Logs sheet.

One row per actionable decision plus one SUMMARY row per run, appended in a single call. A failed log write is
reported by the caller as an error and never fails the sync.
"""
import logging
import os
import time
from datetime import datetime

from src import sheets
from src.next_steps import (
    BLOCKED_EMPTY, BLOCKED_INVALID_DATE, CONFLICT, DUE_DATE, NEXT_STEPS, UPDATE, norm_date, norm_text,
)

logger = logging.getLogger(__name__)

HEADER = ["Timestamp (UTC)", "Run ID", "Mode", "Level", "Domain", "Deal ID", "Deal Name", "Owner", "Field",
          "Sheet Value", "HubSpot Before", "Value Written", "Result", "Detail"]
DEFAULT_TAB = "HubSpot Next Steps Sync Logs"

_WARN = {CONFLICT, BLOCKED_EMPTY, BLOCKED_INVALID_DATE}


def _show(field_name: str, value) -> str:
    """Readable value for the log: dates as ISO when they parse, otherwise the raw text."""
    if field_name == DUE_DATE:
        iso = norm_date(value)
        return iso if iso is not None else norm_text(value)
    return norm_text(value)


def _fields(ctx):
    return ((NEXT_STEPS, ctx.sheet_s, ctx.hs_s), (DUE_DATE, ctx.sheet_t, ctx.hs_t))


def build_rows(run_id: str, mode: str, plans: list, push_results: dict, now: datetime,
               duration_s: float = 0.0, snapshot: dict | None = None) -> list[list[str]]:
    """Log rows (actionable decisions + one SUMMARY row) for a finished run."""
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    rows: list[list[str]] = []
    counts = dict(updated=0, would=0, conflicts=0, blocked=0, skipped=0, baseline=0, errors=0)

    def row(plan, field_name, sheet_v, hs_v, written, result, level, detail=""):
        c = plan.ctx
        rows.append([ts, run_id, mode, level, c.domain, c.deal_id, c.deal_name, c.owner, field_name,
                     _show(field_name, sheet_v), _show(field_name, hs_v), written, result, detail])

    for plan in plans:
        c = plan.ctx
        if plan.skip:
            counts["skipped"] += 1
            snap = (snapshot or {}).get(c.domain)
            if snap:
                for name, sheet_v, hs_v in _fields(c):
                    base = snap.get("next_steps" if name == NEXT_STEPS else "due_date", "")
                    differs = (norm_date(sheet_v) != norm_date(base)) if name == DUE_DATE \
                        else norm_text(sheet_v) != norm_text(base)
                    if differs:
                        row(plan, name, sheet_v, hs_v, "", plan.skip, "WARN", "edit not pushed")
            continue
        if plan.decisions and all(d.action == "BASELINE" for d in plan.decisions):
            counts["baseline"] += 1
            continue
        for d, (name, sheet_v, hs_v) in zip(plan.decisions, _fields(c)):
            if d.action == UPDATE:
                if mode == "dry_run":
                    counts["would"] += 1
                    row(plan, name, sheet_v, hs_v, "", "WOULD_UPDATE", "INFO")
                    continue
                ok, detail = push_results.get(c.deal_id, (False, "no push result"))
                if ok:
                    counts["updated"] += 1
                    row(plan, name, sheet_v, hs_v, d.push, "UPDATED", "INFO")
                else:
                    counts["errors"] += 1
                    row(plan, name, sheet_v, hs_v, "", "ERROR", "ERROR", detail)
            elif d.action in _WARN:
                counts["conflicts" if d.action == CONFLICT else "blocked"] += 1
                row(plan, name, sheet_v, hs_v, _show(name, hs_v), d.action, "WARN", d.detail)

    detail = (f"rows={len(plans)} updated={counts['updated']} would_update={counts['would']} "
              f"conflicts={counts['conflicts']} blocked={counts['blocked']} skipped={counts['skipped']} "
              f"baseline={counts['baseline']} errors={counts['errors']} duration={round(duration_s)}s")
    rows.append([ts, run_id, mode, "ERROR" if counts["errors"] else "INFO", "", "", "", "", "", "", "", "",
                 "SUMMARY", detail])
    return rows


def append(rows: list[list[str]]) -> None:
    """Append rows to the log tab in one call (header written first if the tab is empty). Retries once."""
    sheet_id = os.getenv("NEXT_STEPS_LOG_SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("NEXT_STEPS_LOG_SHEET_ID is not set")
    tab = os.getenv("NEXT_STEPS_LOG_TAB", DEFAULT_TAB).strip() or DEFAULT_TAB

    ws = sheets._get_client().open_by_key(sheet_id).worksheet(tab)
    if not ws.row_values(1):
        ws.update(values=[HEADER], range_name="A1")
    for attempt in (1, 2):
        try:
            ws.append_rows(rows, value_input_option="RAW")
            return
        except Exception as exc:
            logger.warning("Next-steps log append failed (attempt %d): %s", attempt, exc)
            if attempt == 2:
                raise
            time.sleep(2)
