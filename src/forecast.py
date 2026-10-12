"""Sales Forecast tabs: HubSpot forecast deals -> "Sales Forecast - Overview" and "Sales Forecast - AEs".

Read-only against HubSpot (search + batch reads only; nothing is ever written to HubSpot).
Both tabs are fully owned by this module and rewritten on every sync, from the same fetched deals
(so the AE totals always equal the Overview totals). Overview = one block per month; AEs = one
"<AE>'s Pipeline" list per primary owner covering both months.

Rules (verified against HubSpot's own Sales Forecast screen, 2026-10-09):
  - A deal is in a month's forecast when pipeline = Sales Pipeline, closedate falls in that
    UTC calendar month, and either
      * its stage is "Closed Won"  (-> "Closed till date"), or
      * it is still open and hs_manual_forecast_category is HIGH / MEDIUM / LOW.
    Closed Lost / Irrelevant / Pushed Out are excluded whatever their category says.
  - Weighted = amount x deal-stage probability (NOT the tier %), Closed Won counts at 100%.
  - HubSpot's screen only lists the five AEs on the forecast team; Piyush's deals are included
    here, so totals can exceed HubSpot's screen by his deals.

Stage labels are always used for logic, never stage IDs: in this portal the id "closedwon" is the
*Pricing Discussion* stage and "closedlost" is *Procurement*.

The build_* / roll_* / compose_* functions are pure so they can be unit-tested without I/O.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import calendar

import src.hubspot as hubspot
from src.mapping import fmt_icp_segment

logger = logging.getLogger(__name__)

TIERS = ("HIGH", "MEDIUM", "LOW")
TIER_TITLE = {
    "HIGH": "High Probability - 80% closure chances",
    "MEDIUM": "Medium Probability - 50% closure chances",
    "LOW": "Low Probability - 20% closure chances",
}
CLOSED_WON_LABEL = "Closed Won"
CLOSED_TITLE = "Closed till date"

OVERVIEW_TAB = "Sales Forecast - Overview"
AES_TAB = "Sales Forecast - AEs"
PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "39628812").strip() or "39628812"
DEAL_URL = f"https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-3"

COLUMNS = ["Company", "Type", "Owner", "Status", "Amount", "Weighted", "Close date", "Next steps", "HubSpot"]
BLOCK_WIDTH = len(COLUMNS)
BLOCK_GAP = 1
AMOUNT_COL = 4      # block-relative index of "Amount"
WEIGHTED_COL = 5    # block-relative index of "Weighted"

# "Sales Forecast - AEs" layout (single 11-column block, no gap)
AE_COLUMNS = ["Company", "Type", "Owner", "Stage", "Amount", "Weighted", "Close date", "Tier",
              "Probability", "Next steps", "HubSpot"]
AE_WIDTH = len(AE_COLUMNS)
AE_PROB_COL = 8
AE_COL_WIDTHS = [170, 110, 150, 200, 90, 90, 80, 80, 80, 330, 60]   # px per column
UNOWNED = "(unowned)"
_SECTION_ORDER = {"CLOSED": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

_DEAL_PROPERTIES = [
    "dealname", "amount", "closedate", "dealstage", "hubspot_owner_id", "hs_all_owner_ids",
    "hs_manual_forecast_category", "next_steps", "hs_next_step",
]


@dataclass
class ForecastDeal:
    """One HubSpot deal, normalised for the Sales Forecast tab."""

    id: str
    name: str
    owner_primary: str      # first name of hubspot_owner_id ("" if unowned)
    owners: str             # "Harsha | Deeksha" (primary first)
    amount: float
    close_date: date
    stage_label: str
    stage_prob: float
    section: str            # "CLOSED" | "HIGH" | "MEDIUM" | "LOW"
    icp_type: str = ""
    next_steps: str = ""

    @property
    def weighted(self) -> float:
        """Amount x deal-stage probability (HubSpot's weighted-pipeline definition)."""
        return self.amount * self.stage_prob


@dataclass
class ForecastResult:
    """Summary of one forecast refresh, surfaced in Slack / run history."""

    months: list[str] = field(default_factory=list)
    deals: int = 0
    weighted: float = 0.0
    written: bool = False


# ── Pure helpers ──────────────────────────────────────────────────────────────

def roll_months(today: date, count: int = 2) -> list[str]:
    """Return `count` consecutive 'YYYY-MM' strings starting with today's month (year-safe)."""
    months: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return months


def month_bounds_ms(month: str) -> tuple[int, int]:
    """Return (start_ms, end_ms) covering the whole UTC calendar month."""
    year, mo = int(month[:4]), int(month[5:7])
    start = datetime(year, mo, 1, tzinfo=timezone.utc)
    end = datetime(year, mo, calendar.monthrange(year, mo)[1], 23, 59, 59, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def month_title(month: str) -> str:
    """'2026-10' -> 'October 2026'."""
    return datetime(int(month[:4]), int(month[5:7]), 1).strftime("%B %Y")


def classify_deal(stage_label: str, stage_is_closed: bool, category: str | None) -> str | None:
    """Return the forecast section for a deal, or None if it is not part of the forecast."""
    if stage_label.strip() == CLOSED_WON_LABEL:
        return "CLOSED"
    if stage_is_closed:        # Closed Lost / Irrelevant / Pushed Out / Meeting Didn't Happen
        return None
    cat = (category or "").strip().upper()
    return cat if cat in TIERS else None


def _parse_amount(raw: str | None) -> float:
    """HubSpot amount string -> float (blank/unparseable -> 0)."""
    try:
        return float(str(raw).replace(",", "").strip()) if raw not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _parse_close_date(raw: str | None) -> date | None:
    """HubSpot closedate (ISO string) -> date, or None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _fmt_close(d: date) -> str:
    """'15 Oct' style, like the manager's tab.

    Leading apostrophe forces TEXT under USER_ENTERED; without it Sheets parses "7 Oct" as a
    date and the cell shows a serial number (46302).
    """
    return f"'{d.day} {d.strftime('%b')}"


def _sorted(deals: list[ForecastDeal]) -> list[ForecastDeal]:
    """Biggest amount first, then name."""
    return sorted(deals, key=lambda d: (-d.amount, d.name.lower()))


# ── Block builder (pure) ──────────────────────────────────────────────────────

@dataclass
class Block:
    """One month's rows plus style markers (row indexes are block-relative, 0-based)."""

    rows: list[list]
    styles: list[tuple[str, int]]       # (kind, row)
    deals: int
    weighted: float
    closed_won: float


def build_block(deals: list[ForecastDeal], month: str, synced_at: datetime) -> Block:
    """Lay out one month: title, KPI strip, sections (closed, high, medium, low), owner table, footer."""
    rows: list[list] = []
    styles: list[tuple[str, int]] = []

    def add(row: list, style: str | None = None) -> None:
        if style:
            styles.append((style, len(rows)))
        rows.append(row + [""] * (BLOCK_WIDTH - len(row)))

    by_section = {s: _sorted([d for d in deals if d.section == s]) for s in ("CLOSED",) + TIERS}
    closed_won = sum(d.amount for d in by_section["CLOSED"])
    weighted_total = sum(d.weighted for d in deals)
    open_amount = sum(d.amount for s in TIERS for d in by_section[s])

    add([f"{month_title(month)} Forecast", "", "", "", "", "", "", f"Synced {synced_at:%d %b %H:%M} UTC"], "title")
    add(["Closed won", "", "Weighted pipeline", "", "Open (unweighted)", "", "Deals"], "kpi_label")
    add([closed_won, "", weighted_total, "", open_amount, "", len(deals)], "kpi_value")
    add([])
    add(COLUMNS, "header")

    sections = [("CLOSED", CLOSED_TITLE)] + [(t, TIER_TITLE[t]) for t in TIERS]
    for key, title in sections:
        group = by_section[key]
        add([title, "", "", "", sum(d.amount for d in group), sum(d.weighted for d in group)], "section")
        for d in group:
            link = f'=HYPERLINK("{DEAL_URL}/{d.id}","Open")'
            add([d.name, d.icp_type, d.owners, d.stage_label, d.amount, d.weighted,
                 _fmt_close(d.close_date), d.next_steps, link])
        add([])

    # Per-owner table, mirrors HubSpot's Sales Forecast list (weighted includes closed won).
    add(["By owner (primary)", "", "", "", "Closed won", "Weighted pipeline", "Deals"], "header")
    owners = sorted({d.owner_primary or "(unowned)" for d in deals})
    for owner in owners:
        mine = [d for d in deals if (d.owner_primary or "(unowned)") == owner]
        add([owner, "", "", "", sum(d.amount for d in mine if d.section == "CLOSED"),
             sum(d.weighted for d in mine), len(mine)])
    add(["Total", "", "", "", closed_won, weighted_total, len(deals)], "subtotal")

    return Block(rows=rows, styles=styles, deals=len(deals), weighted=weighted_total, closed_won=closed_won)


def _tier_label(section: str) -> str:
    """'CLOSED' -> 'Closed', 'HIGH' -> 'High' ..."""
    return section.title()


def _money_text(value: float) -> str:
    return f"${value:,.0f}"


def build_ae_grid(
    deals_by_month: dict[str, list[ForecastDeal]], months: list[str], synced_at: datetime
) -> tuple[list[list], list[tuple[str, int, int, int]]]:
    """Lay out the "Sales Forecast - AEs" tab: summary by AE, then one list per primary owner.

    Each deal appears exactly once, under its primary owner. Returns (grid, styles) with styles
    as (kind, row, col_start, col_end) like compose_blocks. Pure - no I/O.
    """
    grid: list[list] = []
    styles: list[tuple[str, int, int, int]] = []

    def add(row: list, style: str | None = None, c0: int = 0, c1: int = AE_WIDTH) -> None:
        if style:
            styles.append((style, len(grid), c0, c1))
        grid.append(row + [""] * (AE_WIDTH - len(row)))

    months = months[:2]                     # the summary has one weighted column per month (two)
    all_deals = [d for m in months for d in deals_by_month.get(m, [])]
    window = " + ".join(month_title(m) for m in months)       # year-safe: "December 2026 + January 2027"

    add([f"Sales Forecast by AE - {window}", "", "", "", "", "", "", "", "", f"Synced {synced_at:%d %b %H:%M} UTC"], "title")
    add([])
    if not all_deals:
        add([f"No forecast deals for {window}."], "note")
        return grid, styles

    def owner_of(d: ForecastDeal) -> str:
        return d.owner_primary or UNOWNED

    owners = sorted({owner_of(d) for d in all_deals}, key=lambda o: (o == UNOWNED, o.lower()))
    month_cols = [f"{month_title(m).split()[0][:3]} weighted" for m in months]

    # Summary: one row per AE + Total. Cols 4/5 get currency from the writer; 2/3 need an explicit "money".
    add(["AE", "Deals"] + month_cols + ["Closed won", "Total weighted"], "header")
    for owner in owners + ["Total"]:
        mine = all_deals if owner == "Total" else [d for d in all_deals if owner_of(d) == owner]
        per_month = [sum(d.weighted for d in deals_by_month.get(m, []) if owner == "Total" or owner_of(d) == owner)
                     for m in months]
        add([owner, len(mine)] + per_month + [sum(d.amount for d in mine if d.section == "CLOSED"),
                                              sum(d.weighted for d in mine)],
            "subtotal" if owner == "Total" else None)
        styles.append(("money", len(grid) - 1, 2, 2 + len(months)))
    add([])

    # One list per primary owner.
    for owner in owners:
        mine = sorted(
            (d for d in all_deals if owner_of(d) == owner),
            key=lambda d: (d.close_date.year, d.close_date.month, _SECTION_ORDER[d.section], -d.amount, d.name.lower()),
        )
        month_split = " | ".join(
            f"{month_title(m).split()[0][:3]} {_money_text(sum(d.weighted for d in mine if d.close_date.strftime('%Y-%m') == m))}"
            for m in months
        )
        add([f"{owner}'s Pipeline", "", "", "", sum(d.amount for d in mine), sum(d.weighted for d in mine),
             "", "", "", f"Weighted: {month_split}"], "section")
        add(AE_COLUMNS, "header")
        for d in mine:
            link = f'=HYPERLINK("{DEAL_URL}/{d.id}","Open")'
            add([d.name, d.icp_type, d.owners, d.stage_label, d.amount, d.weighted, _fmt_close(d.close_date),
                 _tier_label(d.section), d.stage_prob, d.next_steps, link],
                "won" if d.section == "CLOSED" else None)
        add([])

    return grid, styles


def compose_blocks(blocks: list[Block]) -> tuple[list[list], list[tuple[str, int, int, int]]]:
    """Place blocks side by side. Returns (grid, styles) with styles as (kind, row, col_start, col_end)."""
    height = max((len(b.rows) for b in blocks), default=0)
    grid: list[list] = [[] for _ in range(height)]
    styles: list[tuple[str, int, int, int]] = []
    for i, block in enumerate(blocks):
        offset = i * (BLOCK_WIDTH + BLOCK_GAP)
        for r in range(height):
            cells = block.rows[r] if r < len(block.rows) else [""] * BLOCK_WIDTH
            grid[r].extend(cells)
            if i < len(blocks) - 1:
                grid[r].extend([""] * BLOCK_GAP)
        for kind, row in block.styles:
            styles.append((kind, row, offset, offset + BLOCK_WIDTH))
    return grid, styles


# ── HubSpot fetch (I/O) ───────────────────────────────────────────────────────

def _fetch_stage_meta(token: str, pipeline_id: str) -> dict[str, dict]:
    """Return {stage_id: {label, prob, closed}} for the Sales Pipeline."""
    resp = hubspot._get(f"{hubspot._BASE}/crm/v3/pipelines/deals/{pipeline_id}", token)
    resp.raise_for_status()
    meta: dict[str, dict] = {}
    for stage in resp.json().get("stages", []):
        m = stage.get("metadata", {})
        try:
            prob = float(m.get("probability", 0) or 0)
        except ValueError:
            prob = 0.0
        meta[stage["id"]] = {
            "label": stage.get("label", "").strip(),
            "prob": prob,
            "closed": str(m.get("isClosed", "false")).lower() == "true",
        }
    return meta


def _search_deals(token: str, pipeline_id: str, month: str, won_stage_ids: list[str]) -> list[dict]:
    """Deals closing in the month that are forecast-categorised OR already Closed Won."""
    start_ms, end_ms = month_bounds_ms(month)
    base = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "closedate", "operator": "BETWEEN", "value": str(start_ms), "highValue": str(end_ms)},
    ]
    groups = [{"filters": base + [{"propertyName": "hs_manual_forecast_category", "operator": "IN",
                                   "values": list(TIERS) + ["CLOSED"]}]}]
    if won_stage_ids:
        groups.append({"filters": base + [{"propertyName": "dealstage", "operator": "IN",
                                           "values": won_stage_ids}]})
    found: dict[str, dict] = {}
    after: str | None = None
    for _ in range(50):
        body: dict = {"filterGroups": groups, "properties": _DEAL_PROPERTIES, "limit": 100}
        if after:
            body["after"] = after
        resp = hubspot._post(f"{hubspot._BASE}/crm/v3/objects/deals/search", token, json=body)
        resp.raise_for_status()
        data = resp.json()
        for deal in data.get("results", []):
            found[deal["id"]] = deal
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return list(found.values())


def _fetch_company_icp(token: str, deals: list[dict]) -> dict[str, str]:
    """Return {deal_id: ICP segment label} via deal->company association + company 'icp' property."""
    assoc = hubspot._batch_get_deal_company_associations(token, deals)
    company_ids = sorted({cids[0] for cids in assoc.values() if cids})
    icp_by_company: dict[str, str] = {}
    for i in range(0, len(company_ids), 100):
        resp = hubspot._post(
            f"{hubspot._BASE}/crm/v3/objects/companies/batch/read", token,
            json={"inputs": [{"id": c} for c in company_ids[i:i + 100]], "properties": ["icp"]},
        )
        if resp.status_code not in (200, 207):
            logger.warning("Forecast: company ICP read HTTP %s", resp.status_code)
            continue
        for company in resp.json().get("results", []):
            icp_by_company[str(company["id"])] = fmt_icp_segment(company.get("properties", {}).get("icp"))
    return {did: icp_by_company.get(cids[0], "") for did, cids in assoc.items() if cids}


def fetch_forecast_deals(month: str, token: str) -> list[ForecastDeal]:
    """Fetch and normalise all forecast deals closing in `month`."""
    pipeline_id = hubspot._get_sales_pipeline_id(token)
    if not pipeline_id:
        raise RuntimeError("HubSpot Sales Pipeline not found")
    stages = _fetch_stage_meta(token, pipeline_id)
    won_ids = [sid for sid, s in stages.items() if s["label"] == CLOSED_WON_LABEL]
    raw = _search_deals(token, pipeline_id, month, won_ids)
    icp = _fetch_company_icp(token, raw)

    deals: list[ForecastDeal] = []
    for d in raw:
        p = d.get("properties", {})
        stage = stages.get(p.get("dealstage") or "", {"label": p.get("dealstage") or "", "prob": 0.0, "closed": False})
        section = classify_deal(stage["label"], stage["closed"], p.get("hs_manual_forecast_category"))
        close = _parse_close_date(p.get("closedate"))
        if section is None or close is None:
            continue
        owner_id = str(p.get("hubspot_owner_id") or "").strip()
        primary_full = hubspot._get_owner_name(owner_id, token) if owner_id else ""
        owners = hubspot._compute_owner_first_names(p, token).replace(",", " | ")
        deals.append(ForecastDeal(
            id=str(d["id"]),
            name=(p.get("dealname") or "").strip() or "(no name)",
            owner_primary=primary_full.split()[0] if primary_full else "",
            owners=owners,
            amount=_parse_amount(p.get("amount")),
            close_date=close,
            stage_label=stage["label"],
            stage_prob=stage["prob"],
            section=section,
            icp_type=icp.get(str(d["id"]), ""),
            next_steps=((p.get("next_steps") or p.get("hs_next_step") or "").strip())[:200],
        ))
    return deals


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_forecast_sync(dry_run: bool = False, today: date | None = None) -> ForecastResult:
    """Refresh the Overview and AEs tabs for the current and next month. Never writes to HubSpot.

    Deals are fetched once per month and feed both tabs, so their totals always agree.
    """
    import src.sheets as sheets

    token = hubspot._token()
    if not token:
        raise RuntimeError("HUBSPOT_API_TOKEN is not set")

    now = datetime.utcnow()
    months = roll_months(today or now.date())
    deals_by_month = {m: fetch_forecast_deals(m, token) for m in months}
    blocks = [build_block(deals_by_month[m], m, now) for m in months]
    result = ForecastResult(
        months=months,
        deals=sum(b.deals for b in blocks),
        weighted=sum(b.weighted for b in blocks),
    )
    for m, b in zip(months, blocks):
        logger.info("Forecast %s: %d deals, weighted $%s, closed won $%s",
                    m, b.deals, f"{b.weighted:,.0f}", f"{b.closed_won:,.0f}")

    if dry_run:
        logger.info("DRY RUN: Sales Forecast tabs not written")
        return result

    grid, styles = compose_blocks(blocks)
    sheets.write_forecast_tab(OVERVIEW_TAB, grid, styles, BLOCK_WIDTH, BLOCK_GAP, [AMOUNT_COL, WEIGHTED_COL])

    # The Overview is already written by now; an AEs failure is reported but does not undo it.
    try:
        ae_grid, ae_styles = build_ae_grid(deals_by_month, months, now)
        sheets.write_forecast_tab(AES_TAB, ae_grid, ae_styles, AE_WIDTH, 0, [AMOUNT_COL, WEIGHTED_COL],
                                  col_widths=AE_COL_WIDTHS, percent_cols=[AE_PROB_COL])
    except Exception as exc:
        raise RuntimeError(f"AEs tab: {exc}") from exc
    result.written = True
    return result
