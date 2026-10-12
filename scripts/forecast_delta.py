"""One-off, READ-ONLY delta report: manager's Oct'26 Pipeline tab vs HubSpot's forecast.

Reads the manager's sheet ("Sales Pipeline - 2026", tab "Oct'26 Pipeline") and HubSpot; writes
nothing to either. Not deployed — run locally:

    .venv/bin/python scripts/forecast_delta.py [--month 2026-10] [--out /path/to/dir]

The sheet ID comes from MANAGER_SHEET_ID (env) or, failing that, from
~/weekly-sales-tracker/.env PIPELINE_SHEET_ID (the same sheet, already shared with the service account).

Reports
  1. In sheet, NOT in HubSpot forecast  -> one primary reason each (+ other issues)
  2. In HubSpot forecast, NOT in sheet  -> and whether it sits elsewhere on the manager's tab
  3. In both, but tier or amount differs
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import gspread
from google.oauth2.service_account import Credentials

import src.forecast as forecast
import src.hubspot as hubspot

MANAGER_TAB = "Oct'26 Pipeline"
# October block = DB:DG down to the row before the "November Pipeline" header; Broader = DI:DT.
_NOV_HEADER = "November Pipeline"
_SUFFIX = r"\b(inc|labs?|ai|io|hq|dev|technologies|solutions|qa|corp|co|ltd|llc|com)\b"
_CLOSED_NON_WON = {"Closed Lost", "Irrelevant", "Pushed Out", "Meeting Didn’t Happen", "Meeting Didn't Happen"}


def key(name: str) -> str:
    """Normalise a company/deal name for matching (case, punctuation, corporate suffixes)."""
    n = (name or "").lower()
    n = re.sub(r"\.(io|ai|dev|com|sh|so|co)\b", "", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(_SUFFIX, " ", n)
    return re.sub(r"\s+", "", n)


def _money(s: str) -> float:
    """'$12,000' -> 12000.0 (blank -> 0)."""
    try:
        return float(re.sub(r"[^0-9.]", "", s or "") or 0)
    except ValueError:
        return 0.0


# ── Manager's sheet ───────────────────────────────────────────────────────────

def _manager_sheet():
    env = dotenv_values(".env")
    creds = Credentials.from_service_account_file(
        env["GOOGLE_CREDENTIALS_FILE"], scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    sheet_id = os.getenv("MANAGER_SHEET_ID") or dotenv_values(
        Path.home() / "weekly-sales-tracker" / ".env")["PIPELINE_SHEET_ID"]
    return gspread.authorize(creds).open_by_key(sheet_id).worksheet(MANAGER_TAB)


def read_october_block(ws) -> list[dict]:
    """Rows of the October block (DB:DG = name, type, owner, status, $, next): {row, tier, name, owner, status, amount}."""
    vals = ws.get("DB1:DG140")
    rows, tier = [], "CONTRACT"
    for i, r in enumerate(vals, 1):
        r = (r + [""] * 6)[:6]
        name = r[0].strip()
        if name == _NOV_HEADER:
            break
        if not name or name in ("Total", "October Pipeline"):
            continue
        if "Probability" in name:
            tier = name.split()[0].upper()
            continue
        rows.append({"row": i, "tier": tier, "name": name, "owner": r[2].strip(),
                     "status": r[3].strip(), "amount": _money(r[4])})
    return rows


def read_broader(ws) -> dict[str, dict]:
    """'Oct Pipeline - Broader' (DI:DT) -> {key: {name, owner, month, tier}} for rows that have a Type."""
    out: dict[str, dict] = {}
    for r in ws.get("DI1:DT140"):
        r = (r + [""] * 12)[:12]
        if r[0].strip() and r[1].strip():
            out.setdefault(key(r[0]), {"name": r[0].strip(), "owner": r[2].strip(),
                                       "month": r[5].strip(), "tier": r[7].strip() or r[11].strip()})
    return out


# ── HubSpot ───────────────────────────────────────────────────────────────────

def fetch_all_deals(token: str, pipeline_id: str, stages: dict) -> list[dict]:
    """All Sales Pipeline deals with the props needed for reasons + company name/domain."""
    props = ["dealname", "amount", "closedate", "dealstage", "hubspot_owner_id",
             "hs_manual_forecast_category", "createdate"]
    deals, after = [], None
    for _ in range(50):
        body = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id}]}],
                "properties": props, "limit": 100}
        if after:
            body["after"] = after
        r = hubspot._post(f"{hubspot._BASE}/crm/v3/objects/deals/search", token, json=body)
        r.raise_for_status()
        data = r.json()
        deals += data["results"]
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    assoc = hubspot._batch_get_deal_company_associations(token, deals)
    companies = hubspot._batch_get_company_info(token, sorted({c for cs in assoc.values() for c in cs}))
    for d in deals:
        p = d["properties"]
        st = stages.get(p.get("dealstage") or "", {"label": p.get("dealstage") or "?"})
        p["_stage"] = st["label"]
        p["_closed"] = st.get("closed", False)
        cids = assoc.get(d["id"], [])
        comp = companies.get(cids[0], {}) if cids else {}
        p["_company"] = comp.get("name", "")
        p["_domain"] = comp.get("domain", "")
    return deals


def build_index(deals: list[dict]) -> dict[str, list[dict]]:
    """key -> deals, indexed by deal name, company name and domain stem."""
    idx: dict[str, list[dict]] = {}
    for d in deals:
        p = d["properties"]
        ks = {key(p.get("dealname", "")), key(p.get("_company", "")), key((p.get("_domain") or "").split(".")[0])}
        for k in ks:
            if k:
                idx.setdefault(k, [])
                if d not in idx[k]:
                    idx[k].append(d)
    return idx


def pick(cands: list[dict], in_fc_ids: set[str]) -> dict:
    """Best candidate: in-forecast first, then open before closed, then newest created."""
    newest_first = sorted(cands, key=lambda d: d["properties"].get("createdate") or "", reverse=True)
    return sorted(newest_first, key=lambda d: (d["id"] not in in_fc_ids, d["properties"]["_closed"]))[0]


def reasons(deal: dict, month: str) -> tuple[str, str, list[str]]:
    """(code, explanation, other issues) for why a matched deal is not in the forecast for `month`."""
    p = deal["properties"]
    stage, cat = p["_stage"], (p.get("hs_manual_forecast_category") or "OMIT").upper()
    close = (p.get("closedate") or "")[:10]
    other: list[str] = [] if float(p.get("amount") or 0) else ["no amount"]
    if stage == "Closed Won":
        return ("CLOSED_WON_ELSEWHERE", f"Already Closed Won (close date {close or 'none'}, not in {month})", other)
    if stage in _CLOSED_NON_WON or p["_closed"]:
        if cat != "OMIT":
            other.append(f"category still {cat}")
        return ("STAGE_CLOSED", f"Stage is {stage}", other)
    if cat == "OMIT":
        other.append("no close date" if not close else f"close date {close}" if not close.startswith(month) else "")
        return ("CATEGORY_OMIT", "Forecast category is OMIT (never set)", [o for o in other if o])
    if not close:
        return ("NO_CLOSE_DATE", f"Category {cat} but no close date (stage {stage})", other)
    if not close.startswith(month):
        return ("OTHER_MONTH", f"Close date {close} is not in {month} (category {cat}, stage {stage})", other)
    return ("IN_FORECAST", "In forecast", other)


_CODE_LABEL = {
    "NO_DEAL": "No HubSpot deal found",
    "CATEGORY_OMIT": "Forecast category is OMIT",
    "NO_CLOSE_DATE": "Category set but no close date",
    "OTHER_MONTH": "Close date in another month",
    "STAGE_CLOSED": "Stage Closed Lost / Pushed Out / Irrelevant / Didn't Happen",
    "CLOSED_WON_ELSEWHERE": "Already Closed Won in another month",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    month = args.month

    token = hubspot._token()
    pid = hubspot._get_sales_pipeline_id(token)
    stages = forecast._fetch_stage_meta(token, pid)
    fc = forecast.fetch_forecast_deals(month, token)
    fc_by_id = {d.id: d for d in fc}
    deals = fetch_all_deals(token, pid, stages)
    idx = build_index(deals)

    ws = _manager_sheet()
    sheet_rows = read_october_block(ws)
    broader = read_broader(ws)

    matched_ids: set[str] = set()
    missing, in_both = [], []
    for r in sheet_rows:
        cands = idx.get(key(r["name"]), [])
        if not cands:
            missing.append({**r, "code": "NO_DEAL", "reason": "No HubSpot deal found (name / company / domain)", "other": "", "hs": ""})
            continue
        d = pick(cands, set(fc_by_id))
        matched_ids.add(d["id"])
        p = d["properties"]
        hs_desc = (f"{p.get('dealname')} | {p['_stage']} | cat={p.get('hs_manual_forecast_category') or 'OMIT'} | "
                   f"close={(p.get('closedate') or '-')[:10]} | amt={p.get('amount') or '-'} | "
                   f"{hubspot._get_owner_name(str(p.get('hubspot_owner_id') or ''), token).split(' ')[0] if p.get('hubspot_owner_id') else 'no owner'}"
                   + (f" | {len(cands)} candidate deals" if len(cands) > 1 else ""))
        if d["id"] in fc_by_id:
            f = fc_by_id[d["id"]]
            in_both.append({**r, "hs_tier": f.section, "hs_amount": f.amount, "hs": hs_desc})
        else:
            code, why, other = reasons(d, month)
            missing.append({**r, "code": code, "reason": why, "other": "; ".join(other), "hs": hs_desc})

    hs_only = []
    for f in sorted(fc, key=lambda x: (x.section, -x.amount)):
        if f.id in matched_ids:
            continue
        b = broader.get(key(f.name))
        hs_only.append({"deal": f.name, "owner": f.owners, "tier": f.section, "stage": f.stage_label,
                        "amount": f.amount, "close": f.close_date.isoformat(),
                        "elsewhere": (f"Yes - 'Broader' section ({b['owner']}, closure {b['month'] or '-'}, "
                                      f"{b['tier'] or 'no tier'})" if b else "No - not on the Oct'26 tab")})

    # Tier / amount mismatches
    mism = []
    for r in in_both:
        if r["tier"] in ("HIGH", "MEDIUM", "LOW") and r["hs_tier"] in ("HIGH", "MEDIUM", "LOW") and r["tier"] != r["hs_tier"]:
            mism.append((r["name"], f"tier sheet {r['tier']} vs HubSpot {r['hs_tier']}"))
        if r["amount"] and abs(r["amount"] - r["hs_amount"]) > 0.5:
            mism.append((r["name"], f"amount sheet ${r['amount']:,.0f} vs HubSpot ${r['hs_amount']:,.0f}"))

    # ── Print ────────────────────────────────────────────────────────────────
    print(f"# Forecast delta — {month}\n")
    print(f"Sheet (Oct'26 Pipeline, October block): **{len(sheet_rows)}** deals | "
          f"HubSpot forecast (incl. Piyush): **{len(fc)}** deals | in both: **{len(in_both)}**\n")
    print(f"## 1. In sheet, NOT in HubSpot forecast ({len(missing)})\n")
    summary = Counter(m["code"] for m in missing)
    for code, n in summary.most_common():
        print(f"- {n} x {_CODE_LABEL[code]}")
    print("\n| Row | Sheet tier | Deal | Sheet owner | Sheet $ | Primary reason | Other issues | HubSpot says |")
    print("|---|---|---|---|---|---|---|---|")
    for m in missing:
        print(f"| {m['row']} | {m['tier']} | {m['name']} | {m['owner']} | {m['amount']:,.0f} | {m['reason']} | {m['other']} | {m['hs']} |")
    print(f"\n## 2. In HubSpot forecast, NOT in sheet's October block ({len(hs_only)})\n")
    print("| Deal | Owner | HubSpot tier | Stage | Amount | Close | Elsewhere on the manager's tab? |")
    print("|---|---|---|---|---|---|---|")
    for h in hs_only:
        print(f"| {h['deal']} | {h['owner']} | {h['tier']} | {h['stage']} | {h['amount']:,.0f} | {h['close']} | {h['elsewhere']} |")
    print(f"\n## 3. In both, but tier or amount differs ({len(mism)})\n")
    for name, what in mism:
        print(f"- {name}: {what}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"forecast_delta_{month}_sheet_not_in_hubspot.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sheet_row", "sheet_tier", "deal", "sheet_owner", "sheet_amount", "reason_code", "reason", "other_issues", "hubspot"])
        for m in missing:
            w.writerow([m["row"], m["tier"], m["name"], m["owner"], m["amount"], m["code"], m["reason"], m["other"], m["hs"]])
    with open(out / f"forecast_delta_{month}_hubspot_not_in_sheet.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["deal", "owner", "hubspot_tier", "stage", "amount", "close", "elsewhere_on_sheet"])
        for h in hs_only:
            w.writerow([h["deal"], h["owner"], h["tier"], h["stage"], h["amount"], h["close"], h["elsewhere"]])
    print(f"\nCSVs written to {out.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
