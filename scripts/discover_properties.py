"""
HubSpot property discovery — run once before building the sync.

Prints all deal and company properties matching our target fields,
then prints all Sales Pipeline stages.

Usage:
    cd ~/hubspot-sheet-sync
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    cp .env.example .env  # fill in HUBSPOT_API_TOKEN
    .venv/bin/python scripts/discover_properties.py

Output is also saved to docs/property-map.txt for reference.
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.hubapi.com"
TOKEN = os.getenv("HUBSPOT_API_TOKEN", "").strip()

if not TOKEN:
    print("ERROR: HUBSPOT_API_TOKEN not set in .env")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# These are the display names we're looking for.
# The script will print ALL properties for each object type, but highlights matches.
TARGET_DEAL_DISPLAY_NAMES = {
    "opportunity", "owner", "priority",
    "icp", "segment", "size", "funding", "employee", "sales team",
    "champion", "job title",
    "l1", "l2", "l3", "qualified", "qualification", "comment",
    "next steps", "due date", "stage",
    "trial start", "trial end", "trial done", "trial status", "trial loss",
    "notes", "call",
    "conversion", "active", "real opportunity",
    "opportunity loss", "close", "closure",
    "meeting booked", "second meeting",
    "interest", "pricing",
}

TARGET_COMPANY_DISPLAY_NAMES = {
    "icp", "segment", "size", "funding", "employee", "sales team",
    "revenue", "headcount",
}


def get_properties(object_type: str) -> list[dict]:
    url = f"{BASE}/crm/v3/properties/{object_type}"
    resp = httpx.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_pipelines() -> list[dict]:
    resp = httpx.get(f"{BASE}/crm/v3/pipelines/deals", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_stages(pipeline_id: str) -> list[dict]:
    resp = httpx.get(
        f"{BASE}/crm/v3/pipelines/deals/{pipeline_id}/stages",
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def matches_target(label: str, targets: set[str]) -> bool:
    label_lower = label.lower()
    return any(t in label_lower for t in targets)


def fmt_property(p: dict) -> str:
    name = p.get("name", "")
    label = p.get("label", "")
    field_type = p.get("fieldType", "")
    group = p.get("groupName", "")
    options = p.get("options", [])
    opt_str = ""
    if options:
        opt_str = f"  options: {[o['value'] for o in options[:10]]}"
    return f"  [{group}] {name!r:50s} | {label!r:45s} | {field_type}{opt_str}"


lines = []


def out(s: str = "") -> None:
    print(s)
    lines.append(s)


out("=" * 100)
out("DEAL PROPERTIES — matching target fields")
out("=" * 100)
try:
    deal_props = get_properties("deals")
    matched = [p for p in deal_props if matches_target(p.get("label", ""), TARGET_DEAL_DISPLAY_NAMES)]
    matched.sort(key=lambda p: p.get("label", "").lower())
    out(f"Found {len(matched)} matching deal properties (out of {len(deal_props)} total)\n")
    for p in matched:
        out(fmt_property(p))
except httpx.HTTPStatusError as e:
    out(f"ERROR fetching deal properties: {e}")

out()
out("=" * 100)
out("COMPANY PROPERTIES — matching target fields")
out("=" * 100)
try:
    company_props = get_properties("companies")
    matched_co = [p for p in company_props if matches_target(p.get("label", ""), TARGET_COMPANY_DISPLAY_NAMES)]
    matched_co.sort(key=lambda p: p.get("label", "").lower())
    out(f"Found {len(matched_co)} matching company properties (out of {len(company_props)} total)\n")
    for p in matched_co:
        out(fmt_property(p))
except httpx.HTTPStatusError as e:
    out(f"ERROR fetching company properties: {e}")

out()
out("=" * 100)
out("SALES PIPELINE STAGES")
out("=" * 100)
try:
    pipelines = get_pipelines()
    for pl in pipelines:
        out(f"\nPipeline: {pl.get('label')!r}  id={pl.get('id')}")
        if pl.get("label") == "Sales Pipeline":
            stages = get_stages(pl["id"])
            for s in stages:
                out(f"  stage_id={s['id']!r:40s} label={s['label']!r}")
except httpx.HTTPStatusError as e:
    out(f"ERROR fetching pipelines: {e}")

out()
out("=" * 100)
out("ALL DEAL PROPERTIES (full dump — for finding unknown cols X, AB, AC, AD, AK)")
out("=" * 100)
try:
    all_deal = sorted(deal_props, key=lambda p: p.get("label", "").lower())
    out(f"\n{len(all_deal)} total deal properties:\n")
    for p in all_deal:
        out(fmt_property(p))
except Exception:
    out("(deal_props not available)")

# Save to docs/
out_path = Path(__file__).parent.parent / "docs" / "property-map.txt"
out_path.parent.mkdir(exist_ok=True)
out_path.write_text("\n".join(lines))
print(f"\n\nSaved to {out_path}")
