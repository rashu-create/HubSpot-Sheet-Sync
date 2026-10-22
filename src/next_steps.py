"""Next Steps / Due Date two-way sync — pure decision logic (no I/O).

AEs edit "Next Steps" and "Due Date" in the pipeline sheet; the sync pushes valid edits to the HubSpot DEAL
(next_steps_management, next_steps_due_date_management). Rules (Rashu, 2026-10-22):
  * HubSpot wins a conflict (both sides changed) and the overruled edit is reported;
  * a field is never cleared from the sheet (an emptied cell is restored from HubSpot);
  * a Due Date must be a real date, today-30d .. today+365d.

To tell "the AE edited the sheet" from "someone edited HubSpot" we keep a snapshot of the values the sync last left
in the sheet. A sheet value different from the snapshot is an AE edit; a HubSpot value different from the snapshot is
a HubSpot edit. The HubSpot side is the same deal -> company fallback the sheet shows, so the two compare like for like.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Field names and the HubSpot DEAL properties they are written to
NEXT_STEPS, DUE_DATE = "next_steps", "due_date"
HUBSPOT_PROPERTY = {NEXT_STEPS: "next_steps_management", DUE_DATE: "next_steps_due_date_management"}

# Results (the "Result" column of the log tab)
NONE = "NONE"                                   # nothing to do (sheet == snapshot, or already equal to HubSpot)
BASELINE = "BASELINE"                           # first sight of this row: record, never push
UPDATE = "UPDATE"                               # push the sheet value to HubSpot
CONFLICT = "CONFLICT_HUBSPOT_WINS"
BLOCKED_EMPTY = "BLOCKED_EMPTY"
BLOCKED_INVALID_DATE = "BLOCKED_INVALID_DATE"
SKIPPED_NO_DEAL = "SKIPPED_NO_DEAL"
SKIPPED_DUPLICATE_ROW = "SKIPPED_DUPLICATE_ROW"
SKIPPED_DEAL_CHANGED = "SKIPPED_DEAL_CHANGED"
SKIPPED_RACE = "SKIPPED_EDITED_DURING_RUN"     # the AE changed the cells while the run was in flight

DATE_MIN_DAYS = -30
DATE_MAX_DAYS = 365

_SHEETS_EPOCH = date(1899, 12, 30)
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%b-%Y")


def norm_text(value) -> str:
    """Comparable form of a Next Steps cell: trimmed, one line-ending style. Numbers become text."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _plausible(d: date) -> date | None:
    return d if 2000 <= d.year <= 2100 else None


def norm_date(value) -> str | None:
    """'' for blank; 'YYYY-MM-DD' for a valid date (Sheets serial, epoch ms, ISO, '30 Oct 2026'); None if not a date."""
    if value is None or isinstance(value, bool):
        return None if isinstance(value, bool) else ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        m = _ISO.match(text)
        if m:
            try:
                return _plausible(date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).isoformat()
            except (ValueError, AttributeError):
                return None
        for fmt in _DATE_FORMATS:
            try:
                return _plausible(datetime.strptime(text, fmt).date()).isoformat()
            except (ValueError, AttributeError):
                pass
        if not re.fullmatch(r"\d+(\.\d+)?", text):
            return None
        value = float(text)
    try:
        number = float(value)
        if number >= 1e11:                                     # epoch milliseconds
            d = datetime.fromtimestamp(number / 1000, timezone.utc).date()
        elif 1 <= number < 200000:                             # Sheets serial day number
            d = _SHEETS_EPOCH + timedelta(days=int(number))
        else:
            return None
    except (OverflowError, ValueError, OSError):
        return None
    d = _plausible(d)
    return d.isoformat() if d else None


@dataclass(frozen=True)
class Decision:
    field: str
    action: str
    detail: str = ""
    push: str | None = None        # the normalised value to send to HubSpot (UPDATE only)


def _norm_for(field_name: str, value):
    return norm_date(value) if field_name == DUE_DATE else norm_text(value)


def decide_field(field_name: str, sheet_raw, base, hs_raw, today: date) -> Decision:
    """What to do with ONE field. `base` is the snapshot value (None = no baseline yet)."""
    if base is None:
        return Decision(field_name, BASELINE, "first sight of this row, recorded as baseline")

    sheet = _norm_for(field_name, sheet_raw)
    hs = _norm_for(field_name, hs_raw)
    base_n = _norm_for(field_name, base)

    if sheet is None:                                          # only a due date can fail to parse
        return Decision(field_name, BLOCKED_INVALID_DATE, f"{sheet_raw!r} is not a date")
    if sheet == base_n:
        return Decision(field_name, NONE)                      # no AE edit
    if sheet == hs:
        return Decision(field_name, NONE, "edit already equals HubSpot")
    if sheet == "":
        return Decision(field_name, BLOCKED_EMPTY, "a field is never cleared")
    if field_name == DUE_DATE:
        d = date.fromisoformat(sheet)
        if not (today + timedelta(days=DATE_MIN_DAYS) <= d <= today + timedelta(days=DATE_MAX_DAYS)):
            return Decision(field_name, BLOCKED_INVALID_DATE,
                            f"{sheet} outside {DATE_MIN_DAYS} .. +{DATE_MAX_DAYS} days from {today}")
    if hs != base_n:
        return Decision(field_name, CONFLICT, "HubSpot changed too; HubSpot value kept")
    return Decision(field_name, UPDATE, push=sheet)


@dataclass
class RowCtx:
    row: int
    domain: str
    deal_id: str
    deal_name: str
    owner: str
    sheet_s: object          # Next Steps cell as read at the start of the run
    sheet_t: object          # Due Date cell as read at the start of the run
    hs_s: object             # HubSpot now (deal value, else company value)
    hs_t: object


@dataclass
class RowPlan:
    ctx: RowCtx
    decisions: list[Decision] = field(default_factory=list)
    push: dict[str, str] = field(default_factory=dict)     # HubSpot property -> value
    keep: set[str] = field(default_factory=set)            # fields whose sheet cell must be left exactly as typed
    skip: str = ""                                         # SKIPPED_* reason (row not handled)


def plan_row(ctx: RowCtx, snap: dict | None, today: date, duplicate: bool = False) -> RowPlan:
    """Decisions for both fields of one pipeline row."""
    plan = RowPlan(ctx)
    if duplicate:
        plan.skip = SKIPPED_DUPLICATE_ROW
        return plan
    if not ctx.deal_id:
        plan.skip = SKIPPED_NO_DEAL
        return plan
    if snap is not None and snap.get("deal_id") != ctx.deal_id:
        plan.skip = SKIPPED_DEAL_CHANGED                    # a different deal now: re-baseline, never push
        return plan

    for name, sheet_raw, hs_raw in ((NEXT_STEPS, ctx.sheet_s, ctx.hs_s), (DUE_DATE, ctx.sheet_t, ctx.hs_t)):
        base = None if snap is None else snap.get(name, "")
        d = decide_field(name, sheet_raw, base, hs_raw, today)
        plan.decisions.append(d)
        if d.action == UPDATE:
            plan.push[HUBSPOT_PROPERTY[name]] = d.push
            plan.keep.add(name)
    return plan
