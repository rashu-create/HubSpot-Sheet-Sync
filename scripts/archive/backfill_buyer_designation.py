"""ONE-TIME backfill: manager's "Buyer & Designation" column -> HubSpot DEAL property `buyer_designation`.

Reads the live block of the manager's current monthly tab (Nov'26 Pipeline: November + December sections) and, for
each company with a buyer text, finds its Sales Pipeline deal and writes the text - but ONLY where HubSpot's
`buyer_designation` is empty. It never overwrites a value, never writes "Not identified", never guesses between deals.

    FORCE_IPV4=1 .venv/bin/python scripts/archive/backfill_buyer_designation.py                 # DRY RUN (default): preview CSV only
    FORCE_IPV4=1 .venv/bin/python scripts/archive/backfill_buyer_designation.py --apply --expect N   # write exactly N deals

`--apply` re-plans from live data right before writing and aborts unless the number of deals to write equals N
(the count approved from the preview), so a sheet edited in between cannot slip in. After writing, every written
deal is read back from HubSpot and compared. Output: output/buyer_backfill_<timestamp>.csv.

Matching, per manager row: associated COMPANY name first, then DEAL name (both normalised, spacing-insensitive).
Several deals for a company -> the single open one; otherwise AMBIGUOUS (not written, listed for review).

Actions: WRITE | SKIP_HAS_VALUE | SKIP_NOT_IDENTIFIED | SKIP_DUPLICATE_ROW | NO_MATCH | AMBIGUOUS
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if os.getenv("FORCE_IPV4") == "1":
    import urllib3.util.connection as _c
    _c.HAS_IPV6 = False

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import src.forecast as forecast          # noqa: E402  (stage metadata helper)
import src.hubspot as hubspot            # noqa: E402
import src.manager_sheet as manager_sheet  # noqa: E402

PROPERTY = "buyer_designation"
_NOT_IDENTIFIED = re.compile(r"^\s*not\s+identified\b", re.IGNORECASE)
CSV_FIELDS = ["row", "manager_name", "sheet_value", "action", "deal_id", "deal_name", "company", "current_value",
              "new_value", "note"]


# ── pure planning ─────────────────────────────────────────────────────────────

def _index(deals: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """{key: [deals]} by company name and by deal name (every key a name answers to)."""
    by_company: dict[str, list[dict]] = {}
    by_deal: dict[str, list[dict]] = {}
    for d in deals:
        for index, name in ((by_company, d["company"]), (by_deal, d["name"])):
            for k in manager_sheet.name_keys(name):
                bucket = index.setdefault(k, [])
                if d not in bucket:
                    bucket.append(d)
    return by_company, by_deal


def _candidates(name: str, by_company: dict, by_deal: dict) -> list[dict]:
    """Deals for a manager name: company-name matches win; deal-name matches are the fallback."""
    for index in (by_company, by_deal):
        found: list[dict] = []
        for k in manager_sheet.name_keys(name):
            for d in index.get(k, []):
                if d not in found:
                    found.append(d)
        if found:
            return found
    return []


def plan(rows: list[manager_sheet.ManagerRow], deals: list[dict]) -> list[dict]:
    """One plan line per manager row that has a buyer text. Pure - no I/O."""
    by_company, by_deal = _index(deals)
    out: list[dict] = []
    claimed: set[str] = set()                      # deals already planned for a write/skip by an earlier row
    for r in manager_sheet.buyer_rows(rows):
        value = " ".join(r.buyer.split())
        line = {"row": r.row, "manager_name": r.name, "sheet_value": value, "action": "", "deal_id": "",
                "deal_name": "", "company": "", "current_value": "", "new_value": "", "note": ""}
        out.append(line)
        if _NOT_IDENTIFIED.match(value):
            line["action"] = "SKIP_NOT_IDENTIFIED"
            continue
        cands = _candidates(r.name, by_company, by_deal)
        if not cands:
            line["action"] = "NO_MATCH"
            continue
        if len(cands) > 1:
            open_deals = [d for d in cands if not d["closed"]]
            if len(open_deals) != 1:
                line["action"] = "AMBIGUOUS"
                line["note"] = f"{len(cands)} deals: " + "; ".join(f"{d['id']} {d['name']}" for d in cands)
                continue
            cands = open_deals
        d = cands[0]
        line.update(deal_id=d["id"], deal_name=d["name"], company=d["company"], current_value=d["buyer"])
        if d["id"] in claimed:
            line["action"] = "SKIP_DUPLICATE_ROW"
            line["note"] = "an earlier manager row already covers this deal"
        elif d["buyer"].strip():
            line["action"] = "SKIP_HAS_VALUE"
            claimed.add(d["id"])
        else:
            line["action"] = "WRITE"
            line["new_value"] = value
            claimed.add(d["id"])
    return out


def updates_from(lines: list[dict]) -> dict[str, dict]:
    """{deal_id: {property: value}} for the WRITE lines only."""
    return {l["deal_id"]: {PROPERTY: l["new_value"]} for l in lines if l["action"] == "WRITE"}


def summarize(lines: list[dict]) -> dict[str, int]:
    return dict(Counter(l["action"] for l in lines))


def check_expected(lines: list[dict], expected: int | None) -> None:
    """--apply guard: the live plan must write exactly the number of deals that was approved."""
    n = sum(1 for l in lines if l["action"] == "WRITE")
    if expected is None or n != expected:
        raise SystemExit(f"Refusing to write: the live plan has {n} deals to write but expected {expected}. "
                         "Re-run the dry run, review the CSV, and pass the new count with --expect.")


# ── HubSpot I/O ───────────────────────────────────────────────────────────────

def load_deals(token: str) -> list[dict]:
    """All Sales Pipeline deals as {id, name, company, closed, buyer}."""
    pipeline_id = hubspot._get_sales_pipeline_id(token)
    if not pipeline_id:
        raise SystemExit("HubSpot Sales Pipeline not found")
    stages = forecast._fetch_stage_meta(token, pipeline_id)

    raw: list[dict] = []
    after = None
    for _ in range(100):
        body: dict = {"filterGroups": [{"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id}]}],
                      "properties": ["dealname", "dealstage", PROPERTY], "limit": 100}
        if after:
            body["after"] = after
        resp = hubspot._post(f"{hubspot._BASE}/crm/v3/objects/deals/search", token, json=body)
        resp.raise_for_status()
        data = resp.json()
        raw.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    assoc = hubspot._batch_get_deal_company_associations(token, raw)
    names: dict[str, str] = {}
    cids = sorted({c[0] for c in assoc.values() if c})
    for i in range(0, len(cids), 100):
        resp = hubspot._post(f"{hubspot._BASE}/crm/v3/objects/companies/batch/read", token,
                             json={"inputs": [{"id": c} for c in cids[i:i + 100]], "properties": ["name"]})
        resp.raise_for_status()
        for c in resp.json().get("results", []):
            names[str(c["id"])] = (c.get("properties", {}).get("name") or "").strip()

    deals = []
    for d in raw:
        p = d.get("properties", {})
        cid = (assoc.get(str(d["id"])) or [""])[0]
        deals.append({
            "id": str(d["id"]),
            "name": (p.get("dealname") or "").strip(),
            "company": names.get(cid, ""),
            "closed": bool(stages.get(p.get("dealstage") or "", {}).get("closed")),
            "buyer": (p.get(PROPERTY) or "").strip(),
        })
    return deals


def read_back(token: str, ids: list[str]) -> dict[str, str]:
    """{deal_id: current buyer_designation} straight from HubSpot."""
    out: dict[str, str] = {}
    for i in range(0, len(ids), 100):
        resp = hubspot._post(f"{hubspot._BASE}/crm/v3/objects/deals/batch/read", token,
                             json={"inputs": [{"id": x} for x in ids[i:i + 100]], "properties": [PROPERTY]})
        resp.raise_for_status()
        for d in resp.json().get("results", []):
            out[str(d["id"])] = (d.get("properties", {}).get(PROPERTY) or "").strip()
    return out


def write_csv(lines: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to HubSpot (default is a dry run)")
    ap.add_argument("--expect", type=int, help="with --apply: the approved number of deals to write")
    args = ap.parse_args()

    token = hubspot._token()
    if not token:
        raise SystemExit("HUBSPOT_API_TOKEN is not set")

    rows = manager_sheet.load_rows()
    deals = load_deals(token)
    lines = plan(rows, deals)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "output" / f"buyer_backfill_{stamp}.csv"
    write_csv(lines, out)

    print(f"Manager rows with a buyer text: {len(lines)} | Sales Pipeline deals read: {len(deals)}")
    for action, n in sorted(summarize(lines).items(), key=lambda kv: -kv[1]):
        print(f"  {action:22s} {n}")
    print(f"Preview written to {out}")

    if not args.apply:
        print("DRY RUN - nothing was written to HubSpot.")
        return 0

    check_expected(lines, args.expect)
    updates = updates_from(lines)
    results = hubspot.push_deal_updates(updates)
    failed = {i: d for i, (ok, d) in results.items() if not ok}
    now = read_back(token, list(updates))
    mismatched = [i for i, v in updates.items() if now.get(i) != v[PROPERTY]]
    print(f"Written: {len(updates) - len(failed)}/{len(updates)} | failed: {len(failed)} | read-back mismatches: {len(mismatched)}")
    for i, d in failed.items():
        print(f"  FAILED {i}: {d}")
    for i in mismatched:
        print(f"  MISMATCH {i}: wrote {updates[i][PROPERTY]!r}, HubSpot now has {now.get(i)!r}")
    return 1 if failed or mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
