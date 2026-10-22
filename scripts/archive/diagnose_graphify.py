"""Diagnose why graphify.com is missing from the sync.

Traces all 4 lookup paths for a given domain (default: graphify.com).

Run from project root:
    python scripts/diagnose_graphify.py
    python scripts/diagnose_graphify.py nodeops.xyz
"""

import sys
import os
import re
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

import httpx
from src.mapping import normalize_domain
from src import hubspot as hs

TARGET = sys.argv[1] if len(sys.argv) > 1 else "graphify.com"

BASE = "https://api.hubapi.com"
TOKEN = os.getenv("HUBSPOT_API_TOKEN", "")

def _headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def _get(url, params=None):
    return httpx.get(url, headers=_headers(), params=params, timeout=15)

def _post(url, json):
    return httpx.post(url, headers=_headers(), json=json, timeout=15)

def _name_keys(name: str) -> list[str]:
    if not name:
        return []
    full = re.sub(r"[^a-z0-9]", "", name.lower())
    keys = [full] if full else []
    parts = name.split()
    if len(parts) > 1:
        first = re.sub(r"[^a-z0-9]", "", parts[0].lower())
        if len(first) >= 4 and first != full:
            keys.append(first)
    return keys


print(f"\n{'='*60}")
print(f"Diagnosing: {TARGET}")
print(f"{'='*60}\n")

normalized = normalize_domain(TARGET)
base = normalized.split(".")[0]
name_lookup = re.sub(r"[^a-z0-9]", "", base.lower())
print(f"  normalized  : {normalized!r}")
print(f"  name_lookup : {name_lookup!r}\n")

# ── Step 1: Build deal map ────────────────────────────────────────────────────
print("Step 1: Building deal map...")
hs._load_ae_owners(TOKEN)
hs.prebuild_deal_map()

dm = hs._DOMAIN_DEAL_MAP or {}
nm = hs._NAME_DEAL_MAP or {}
dnm = hs._DEAL_NAME_MAP or {}
print(f"  Domain map   : {len(dm)} entries")
print(f"  Name map     : {len(nm)} entries")
print(f"  Deal name map: {len(dnm)} entries\n")

# ── Step 2: Check each map ────────────────────────────────────────────────────
print("Step 2: Map lookups")

hit1 = dm.get(normalized)
print(f"  [1a] _DOMAIN_DEAL_MAP[{normalized!r}] → {hit1}")

hit2 = nm.get(name_lookup)
print(f"  [1b] _NAME_DEAL_MAP[{name_lookup!r}]  → {hit2}")

hit3 = dnm.get(name_lookup)
print(f"  [1c] _DEAL_NAME_MAP[{name_lookup!r}]  → {hit3}")

# ── Step 3: Check if domain appears anywhere in maps (partial) ────────────────
print(f"\nStep 3: Searching maps for anything containing {name_lookup!r}")
domain_hits = [(k, v) for k, v in dm.items() if name_lookup in k]
name_hits   = [(k, v) for k, v in nm.items() if name_lookup in k]
deal_hits   = [(k, v) for k, v in dnm.items() if name_lookup in k]
print(f"  Domain map partial matches  : {domain_hits or 'none'}")
print(f"  Name map partial matches    : {name_hits or 'none'}")
print(f"  Deal name map partial matches: {deal_hits or 'none'}")

# ── Step 4: HubSpot company search — domain ───────────────────────────────────
print(f"\nStep 4: HubSpot company search (domain={TARGET!r})")
r = _post(f"{BASE}/crm/v3/objects/companies/search", json={
    "filterGroups": [{"filters": [{"propertyName": "domain", "operator": "EQ", "value": TARGET}]}],
    "properties": ["name", "domain", "hs_additional_domains"],
    "limit": 10,
})
if r.status_code == 200:
    results = r.json().get("results", [])
    if results:
        for c in results:
            p = c.get("properties", {})
            print(f"  company_id={c['id']}  name={p.get('name')!r}  domain={p.get('domain')!r}  additional={p.get('hs_additional_domains')!r}")
    else:
        print("  No results for exact domain search")
else:
    print(f"  HTTP {r.status_code}: {r.text[:200]}")

# ── Step 5: HubSpot company search — name CONTAINS_TOKEN ─────────────────────
print(f"\nStep 5: HubSpot company search (name CONTAINS_TOKEN {base!r})")
r = _post(f"{BASE}/crm/v3/objects/companies/search", json={
    "filterGroups": [{"filters": [{"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": base}]}],
    "properties": ["name", "domain", "hs_additional_domains"],
    "limit": 10,
})
if r.status_code == 200:
    results = r.json().get("results", [])
    if results:
        for c in results:
            p = c.get("properties", {})
            print(f"  company_id={c['id']}  name={p.get('name')!r}  domain={p.get('domain')!r}")
    else:
        print("  No results for name CONTAINS_TOKEN search")
else:
    print(f"  HTTP {r.status_code}: {r.text[:200]}")

# ── Step 6: HubSpot deal search — deal name CONTAINS_TOKEN ───────────────────
print(f"\nStep 6: HubSpot deal search (dealname CONTAINS_TOKEN {base!r})")
r = _post(f"{BASE}/crm/v3/objects/deals/search", json={
    "filterGroups": [{"filters": [{"propertyName": "dealname", "operator": "CONTAINS_TOKEN", "value": base}]}],
    "properties": ["dealname", "pipeline", "dealstage", "hubspot_owner_id"],
    "limit": 10,
})
if r.status_code == 200:
    results = r.json().get("results", [])
    if results:
        for d in results:
            p = d.get("properties", {})
            print(f"  deal_id={d['id']}  name={p.get('dealname')!r}  pipeline={p.get('pipeline')!r}  stage={p.get('dealstage')!r}  owner={p.get('hubspot_owner_id')!r}")
    else:
        print("  No deals matching deal name search")
else:
    print(f"  HTTP {r.status_code}: {r.text[:200]}")

# ── Step 7: Get pipeline IDs to understand which one is Sales Pipeline ────────
print(f"\nStep 7: Sales Pipeline ID lookup")
r = _get(f"{BASE}/crm/v3/pipelines/deals")
if r.status_code == 200:
    for p in r.json().get("results", []):
        print(f"  pipeline_id={p['id']!r}  label={p.get('label')!r}")
else:
    print(f"  HTTP {r.status_code}")

print(f"\n{'='*60}\nDone.\n")
