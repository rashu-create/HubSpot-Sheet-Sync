"""HubSpot client for HubSpot→Sheet sync.

Ported and extended from trial-active-tenants/src/hubspot.py.
DO NOT import from that project — logic is copied and adapted here.

New features vs. the original:
- RateLimiter (token bucket, 10 req/s) applied to EVERY httpx call
- Exponential back-off on HTTP 429 (base 1s, cap 30s, up to 5 retries)
- fetch_deal_properties() — GET full deal property set
- fetch_company_properties() — GET full company property set
- get_row_data() — orchestrate full row enrichment for one domain

Env vars:
  HUBSPOT_API_TOKEN   — private app token
  HUBSPOT_AE_EMAILS   — comma-separated AE emails (filter); blank = show all
"""

import logging
import os
import threading
import time

import httpx

from src.mapping import (
    COLUMN_MAP,
    COMPANY_PROPERTIES,
    DEAL_PROPERTIES,
    apply_formatter,
    fmt_date_dmy,
    fmt_month_year,
    fmt_passthrough,
    fmt_capitalize,
    fmt_number,
    normalize_domain,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com"

# Process-lifetime caches
_PIPELINE_CACHE: dict[str, str | None] = {}
_STAGE_LABEL_CACHE: dict[str, str] = {}
_AE_OWNER_IDS: set[str] = set()
_AE_LOADED: bool = False
_OWNER_NAME_CACHE: dict[str, str] = {}   # owner_id → display name
# Deal map: domain → deal entry; None means not yet built
_DOMAIN_DEAL_MAP: dict[str, dict] | None = None

_SALES_PIPELINE_LABEL = "Sales Pipeline"


# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Token-bucket rate limiter, thread-safe, 10 tokens/second."""

    def __init__(self, rate: float = 10.0, capacity: float = 10.0) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until next token?
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)


# Shared singleton used by all API calls in this module
_rate_limiter = RateLimiter(rate=10.0, capacity=10.0)


def _request(method: str, url: str, *, token: str, **kwargs) -> httpx.Response:
    """Make an HTTP request with rate limiting and 429 back-off."""
    headers = kwargs.pop("headers", {})
    headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    base_wait = 1.0
    max_wait = 30.0
    max_retries = 5

    for attempt in range(max_retries + 1):
        _rate_limiter.acquire()
        try:
            resp = httpx.request(method, url, headers=headers, timeout=15, **kwargs)
        except httpx.RequestError as exc:
            logger.warning("HTTP request error [%s %s]: %s", method, url, exc)
            if attempt < max_retries:
                time.sleep(min(base_wait * (2 ** attempt), max_wait))
                continue
            raise

        if resp.status_code == 429:
            wait = min(base_wait * (2 ** attempt), max_wait)
            logger.warning("HubSpot 429 rate limit — backing off %.1fs (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
            continue

        return resp

    raise RuntimeError(f"HubSpot request failed after {max_retries} retries: {method} {url}")


def _get(url: str, token: str, **kwargs) -> httpx.Response:
    return _request("GET", url, token=token, **kwargs)


def _post(url: str, token: str, **kwargs) -> httpx.Response:
    return _request("POST", url, token=token, **kwargs)


# ── Token helper ──────────────────────────────────────────────────────────────

def _token() -> str | None:
    return os.getenv("HUBSPOT_API_TOKEN", "").strip() or None


# ── AE owner filter ───────────────────────────────────────────────────────────

def _is_ae(owner_id: str | None) -> bool:
    """True if this owner is a known AE, or if the AE filter is disabled."""
    if not owner_id:
        return False
    return not _AE_OWNER_IDS or owner_id in _AE_OWNER_IDS


def _load_ae_owners(token: str) -> None:
    """Populate _AE_OWNER_IDS from HUBSPOT_AE_EMAILS (once per process)."""
    global _AE_LOADED
    if _AE_LOADED:
        return
    _AE_LOADED = True

    ae_emails_raw = os.getenv("HUBSPOT_AE_EMAILS", "").strip()
    if not ae_emails_raw:
        logger.info("HubSpot AE filter disabled (HUBSPOT_AE_EMAILS not set — all owners shown)")
        return

    ae_emails = {e.strip().lower() for e in ae_emails_raw.split(",") if e.strip()}
    after = None
    pages = 0

    while pages < 10:
        params: dict = {"limit": 100}
        if after:
            params["after"] = after

        try:
            resp = _get(f"{_BASE}/crm/v3/owners", token, params=params)
        except Exception as exc:
            logger.warning("HubSpot owners fetch error: %s", exc)
            break

        if resp.status_code != 200:
            logger.warning("HubSpot owners fetch: HTTP %s", resp.status_code)
            break

        data = resp.json()
        for owner in data.get("results", []):
            if owner.get("email", "").lower() in ae_emails:
                _AE_OWNER_IDS.add(str(owner["id"]))

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        pages += 1

    logger.info(
        "HubSpot AE filter: %d email(s) configured, %d owner ID(s) matched",
        len(ae_emails),
        len(_AE_OWNER_IDS),
    )


# ── Pipeline + stage cache ────────────────────────────────────────────────────

def _get_stage_labels(pipeline_id: str, token: str) -> None:
    """Populate _STAGE_LABEL_CACHE for the given pipeline."""
    try:
        resp = _get(f"{_BASE}/crm/v3/pipelines/deals/{pipeline_id}/stages", token)
    except Exception as exc:
        logger.warning("HubSpot stage fetch error for pipeline %s: %s", pipeline_id, exc)
        return

    if resp.status_code != 200:
        logger.warning("HubSpot stage fetch pipeline %s: HTTP %s", pipeline_id, resp.status_code)
        return

    for stage in resp.json().get("results", []):
        _STAGE_LABEL_CACHE[stage["id"]] = stage["label"]
    logger.info("HubSpot stage labels loaded: %d stages", len(_STAGE_LABEL_CACHE))


def _get_sales_pipeline_id(token: str) -> str | None:
    """Return the Sales Pipeline ID (cached for the process lifetime)."""
    if "sales" in _PIPELINE_CACHE:
        return _PIPELINE_CACHE["sales"]

    try:
        resp = _get(f"{_BASE}/crm/v3/pipelines/deals", token)
    except Exception as exc:
        logger.warning("HubSpot pipeline fetch error: %s", exc)
        _PIPELINE_CACHE["sales"] = None
        return None

    if resp.status_code != 200:
        logger.warning("HubSpot pipeline fetch: HTTP %s", resp.status_code)
        _PIPELINE_CACHE["sales"] = None
        return None

    for pipeline in resp.json().get("results", []):
        if pipeline.get("label") == _SALES_PIPELINE_LABEL:
            pipeline_id = pipeline["id"]
            _PIPELINE_CACHE["sales"] = pipeline_id
            logger.info("HubSpot Sales Pipeline ID: %s", pipeline_id)
            _get_stage_labels(pipeline_id, token)
            _load_ae_owners(token)
            return pipeline_id

    logger.warning("HubSpot '%s' not found in pipelines list", _SALES_PIPELINE_LABEL)
    _PIPELINE_CACHE["sales"] = None
    return None


# ── Company search ────────────────────────────────────────────────────────────

def _search_companies(property_name: str, value: str, token: str, limit: int = 10, operator: str = "EQ") -> list[dict]:
    """Search companies by a property. Returns list of {id, owner_id} dicts."""
    try:
        resp = _post(
            f"{_BASE}/crm/v3/objects/companies/search",
            token,
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": property_name, "operator": operator, "value": value}]}
                ],
                "properties": ["domain", "name", "hubspot_owner_id"],
                "limit": limit,
            },
        )
    except Exception as exc:
        logger.warning("HubSpot company search error (%s=%r): %s", property_name, value, exc)
        return []

    if resp.status_code != 200:
        logger.warning("HubSpot company search (%s=%r): HTTP %s", property_name, value, resp.status_code)
        return []

    return [
        {"id": c["id"], "owner_id": c.get("properties", {}).get("hubspot_owner_id")}
        for c in resp.json().get("results", [])
    ]


# ── Deal map pre-build (deal-first lookup) ────────────────────────────────────

def _fetch_all_pipeline_deals(token: str, pipeline_id: str) -> list[dict]:
    """Page through all deals in the given pipeline. Returns list of deal dicts."""
    all_deals: list[dict] = []
    after: str | None = None

    for _ in range(50):  # safety cap
        body: dict = {
            "filterGroups": [
                {"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id}]}
            ],
            "properties": ["hubspot_owner_id", "createdate", "dealstage", "pipeline"],
            "limit": 100,
        }
        if after:
            body["after"] = after

        try:
            resp = _post(f"{_BASE}/crm/v3/objects/deals/search", token, json=body)
        except Exception as exc:
            logger.warning("Deal search error: %s", exc)
            break

        if resp.status_code != 200:
            logger.warning("Deal search HTTP %s", resp.status_code)
            break

        data = resp.json()
        all_deals.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return all_deals


def _batch_get_deal_company_associations(token: str, deals: list[dict]) -> dict[str, list[str]]:
    """Return {deal_id: [company_id, ...]} via v4 batch association API."""
    deal_to_companies: dict[str, list[str]] = {}

    for i in range(0, len(deals), 100):
        batch = deals[i : i + 100]
        inputs = [{"id": d["id"]} for d in batch]

        try:
            resp = _post(
                f"{_BASE}/crm/v4/associations/deals/companies/batch/read",
                token,
                json={"inputs": inputs},
            )
        except Exception as exc:
            logger.warning("Batch deal→company association error: %s", exc)
            continue

        if resp.status_code not in (200, 207):
            logger.warning("Batch deal→company association HTTP %s", resp.status_code)
            continue

        for result in resp.json().get("results", []):
            deal_id = str(result.get("from", {}).get("id", ""))
            company_ids = [str(a["toObjectId"]) for a in result.get("to", [])]
            if deal_id:
                deal_to_companies[deal_id] = company_ids

    return deal_to_companies


def _batch_get_company_domains(token: str, company_ids: list[str]) -> dict[str, str]:
    """Return {company_id: domain_str} via v3 batch company read."""
    result: dict[str, str] = {}

    for i in range(0, len(company_ids), 100):
        batch = company_ids[i : i + 100]

        try:
            resp = _post(
                f"{_BASE}/crm/v3/objects/companies/batch/read",
                token,
                json={
                    "inputs": [{"id": cid} for cid in batch],
                    "properties": ["domain", "name"],
                },
            )
        except Exception as exc:
            logger.warning("Batch company domain read error: %s", exc)
            continue

        if resp.status_code not in (200, 207):
            logger.warning("Batch company domain read HTTP %s", resp.status_code)
            continue

        for company in resp.json().get("results", []):
            cid = str(company["id"])
            domain = (company.get("properties", {}).get("domain") or "").lower().strip()
            result[cid] = domain

    return result


def prebuild_deal_map() -> None:
    """Fetch all Sales Pipeline deals and build a domain→deal map in _DOMAIN_DEAL_MAP.

    Rebuilds unconditionally on every call (called once per sync run).
    After this runs, get_row_data() uses the map as the primary lookup, with
    the company-search path as a fallback for domains not in the map.
    """
    global _DOMAIN_DEAL_MAP
    _DOMAIN_DEAL_MAP = {}

    token = _token()
    if not token:
        logger.warning("HUBSPOT_API_TOKEN not set — skipping deal map build")
        return

    pipeline_id = _get_sales_pipeline_id(token)
    if not pipeline_id:
        logger.warning("No Sales Pipeline found — skipping deal map build")
        return

    all_deals = _fetch_all_pipeline_deals(token, pipeline_id)
    logger.info("Deal map: %d deals found in Sales Pipeline", len(all_deals))
    if not all_deals:
        return

    deal_to_companies = _batch_get_deal_company_associations(token, all_deals)

    unique_company_ids = list({cid for cids in deal_to_companies.values() for cid in cids})
    company_domain_map = _batch_get_company_domains(token, unique_company_ids)

    domain_map: dict[str, dict] = {}

    for deal in all_deals:
        deal_id = deal["id"]
        props = deal.get("properties", {})
        owner = props.get("hubspot_owner_id")
        createdate = props.get("createdate") or ""

        for cid in deal_to_companies.get(deal_id, []):
            domain = company_domain_map.get(cid, "")
            if not domain:
                continue

            entry = {
                "deal_id": deal_id,
                "company_id": cid,
                "owner_id": owner,
                "deal_stage": props.get("dealstage") or "",
                "createdate": createdate,
            }

            existing = domain_map.get(domain)
            if existing is None:
                domain_map[domain] = entry
            else:
                # Prefer AE-owned, then more recent
                new_score = (_is_ae(owner), createdate)
                old_score = (_is_ae(existing.get("owner_id")), existing.get("createdate", ""))
                if new_score > old_score:
                    domain_map[domain] = entry

    _DOMAIN_DEAL_MAP = domain_map
    logger.info("Deal map built: %d company domains indexed", len(domain_map))


# ── Deal selection for a company ──────────────────────────────────────────────

def _get_best_deal_for_company(company_id: str, token: str, sales_pipeline_id: str | None) -> dict | None:
    """Return the best Sales Pipeline deal dict for a company, or None.

    Best = AE-owned first, then most recent by createdate.
    Returns dict with keys: deal_id, owner_id, deal_stage, createdate, properties.
    """
    try:
        resp = _get(
            f"{_BASE}/crm/v4/objects/companies/{company_id}/associations/deals",
            token,
        )
    except Exception as exc:
        logger.warning("HubSpot deal association error for company %s: %s", company_id, exc)
        return None

    if resp.status_code != 200:
        logger.warning("HubSpot deal association %s: HTTP %s", company_id, resp.status_code)
        return None

    results = resp.json().get("results", [])
    if not results:
        logger.debug("Company %s has no deal associations", company_id)
        return None

    deal_ids = [r["toObjectId"] for r in results]

    try:
        resp = _post(
            f"{_BASE}/crm/v3/objects/deals/batch/read",
            token,
            json={
                "inputs": [{"id": str(did)} for did in deal_ids],
                "properties": ["hubspot_owner_id", "createdate", "dealstage", "pipeline"],
            },
        )
    except Exception as exc:
        logger.warning("HubSpot deal batch read error: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning("HubSpot deal batch read: HTTP %s", resp.status_code)
        return None

    deals = resp.json().get("results", [])
    if not deals:
        return None

    # Filter to Sales Pipeline only when we have its ID
    if sales_pipeline_id:
        sales_deals = [d for d in deals if d.get("properties", {}).get("pipeline") == sales_pipeline_id]
        if sales_deals:
            deals = sales_deals
        else:
            logger.debug(
                "No Sales Pipeline deals for company %s (pipelines found: %s)",
                company_id,
                [d.get("properties", {}).get("pipeline") for d in deals],
            )
            return None

    # Prefer AE-owned deals, then most recent
    deals.sort(
        key=lambda d: (
            _is_ae(d.get("properties", {}).get("hubspot_owner_id")),
            d.get("properties", {}).get("createdate") or "",
        ),
        reverse=True,
    )
    best = deals[0]
    props = best.get("properties", {})
    return {
        "deal_id": best["id"],
        "owner_id": props.get("hubspot_owner_id"),
        "deal_stage": props.get("dealstage") or "",
        "createdate": props.get("createdate") or "",
    }


def _pick_best_company(
    candidates: list[dict],
    token: str,
    sales_pipeline_id: str | None,
) -> tuple[str | None, str | None, dict | None]:
    """From candidates, return (company_id, fallback_owner_id, deal_info)."""
    best_id: str | None = None
    best_fallback: str | None = None
    best_deal: dict | None = None
    best_score: tuple = (-1, False, "")

    for c in candidates:
        deal = _get_best_deal_for_company(c["id"], token, sales_pipeline_id)
        if deal:
            score: tuple = (1, _is_ae(deal.get("owner_id")), deal.get("createdate", ""))
        else:
            score = (0, False, "")

        if score > best_score:
            best_score = score
            best_id = c["id"]
            best_fallback = c["owner_id"]
            best_deal = deal

    return best_id, best_fallback, best_deal


# ── Property fetchers ─────────────────────────────────────────────────────────

def fetch_deal_properties(deal_id: str, token: str) -> dict:
    """GET /crm/v3/objects/deals/{id} with all DEAL_PROPERTIES. Returns properties dict."""
    params = {"properties": ",".join(DEAL_PROPERTIES)}
    try:
        resp = _get(f"{_BASE}/crm/v3/objects/deals/{deal_id}", token, params=params)
    except Exception as exc:
        logger.warning("HubSpot fetch deal %s error: %s", deal_id, exc)
        return {}

    if resp.status_code != 200:
        logger.warning("HubSpot fetch deal %s: HTTP %s", deal_id, resp.status_code)
        return {}

    return resp.json().get("properties", {})


def fetch_company_properties(company_id: str, token: str) -> dict:
    """GET /crm/v3/objects/companies/{id} with all COMPANY_PROPERTIES. Returns properties dict."""
    params = {"properties": ",".join(COMPANY_PROPERTIES)}
    try:
        resp = _get(f"{_BASE}/crm/v3/objects/companies/{company_id}", token, params=params)
    except Exception as exc:
        logger.warning("HubSpot fetch company %s error: %s", company_id, exc)
        return {}

    if resp.status_code != 200:
        logger.warning("HubSpot fetch company %s: HTTP %s", company_id, resp.status_code)
        return {}

    return resp.json().get("properties", {})


# ── Owner name resolution ─────────────────────────────────────────────────────

def _get_owner_name(owner_id: str, token: str) -> str:
    """Return display name for a HubSpot owner ID (cached)."""
    if owner_id in _OWNER_NAME_CACHE:
        return _OWNER_NAME_CACHE[owner_id]

    try:
        resp = _get(f"{_BASE}/crm/v3/owners/{owner_id}", token)
    except Exception as exc:
        logger.warning("HubSpot owner fetch error for %s: %s", owner_id, exc)
        return ""

    if resp.status_code != 200:
        logger.warning("HubSpot owner %s: HTTP %s", owner_id, resp.status_code)
        return ""

    data = resp.json()
    first = data.get("firstName", "") or ""
    last = data.get("lastName", "") or ""
    name = f"{first} {last}".strip() or data.get("email", "") or ""
    _OWNER_NAME_CACHE[owner_id] = name
    return name


# ── Main entry point ──────────────────────────────────────────────────────────

def get_row_data(domain: str) -> dict | None:
    """Return a flat dict keyed by column letter with formatted values, or None.

    Orchestrates:
      1. Resolve Sales Pipeline ID (cached)
      2. Search company by domain
      3. Pick best deal (AE-owned, most recent)
      4. Fetch full deal + company properties
      5. Apply formatters per COLUMN_MAP
      6. Return dict keyed by col_letter → formatted value

    Returns None if company not found, no Sales Pipeline deal, or any API error.
    """
    token = _token()
    if not token:
        logger.warning("HUBSPOT_API_TOKEN not set — skipping HubSpot lookup for %r", domain)
        return None

    try:
        sales_pipeline_id = _get_sales_pipeline_id(token)
        normalized = normalize_domain(domain)

        deal_info: dict | None = None
        company_id: str | None = None
        fallback_owner_id: str | None = None

        # 1a. Fast path: pre-built deal map (deal-first, most reliable)
        if _DOMAIN_DEAL_MAP is not None:
            map_hit = _DOMAIN_DEAL_MAP.get(normalized)
            if map_hit:
                company_id = map_hit["company_id"]
                deal_info = {
                    "deal_id": map_hit["deal_id"],
                    "owner_id": map_hit.get("owner_id"),
                    "deal_stage": map_hit.get("deal_stage", ""),
                    "createdate": map_hit.get("createdate", ""),
                }

        # 1b. Slow path: company search + deal lookup (fallback)
        if deal_info is None:
            candidates_by_domain = _search_companies("domain", domain, token, limit=5)
            base_name = normalized.split(".")[0].replace("-", " ")
            candidates_by_name: list[dict] = []
            if base_name:
                candidates_by_name = _search_companies(
                    "name", base_name, token, limit=10, operator="CONTAINS_TOKEN"
                )

            seen_ids: set[str] = set()
            candidates: list[dict] = []
            for c in candidates_by_domain + candidates_by_name:
                if c["id"] not in seen_ids:
                    seen_ids.add(c["id"])
                    candidates.append(c)

            if not candidates:
                logger.info("HubSpot: no company found for domain %r", domain)
                return None

            company_id, fallback_owner_id, deal_info = _pick_best_company(
                candidates, token, sales_pipeline_id
            )

            if not company_id or not deal_info:
                logger.info("HubSpot: no Sales Pipeline deal for domain %r", domain)
                return None

        deal_id = deal_info["deal_id"]

        # 2. Fetch full properties
        deal_props = fetch_deal_properties(deal_id, token)
        company_props = fetch_company_properties(company_id, token) if company_id else {}

        # 4. Resolve owner name (AE filter applies)
        deal_owner_id = deal_props.get("hubspot_owner_id") or deal_info.get("owner_id")
        owner_id = (deal_owner_id if _is_ae(deal_owner_id) else None) or (
            fallback_owner_id if _is_ae(fallback_owner_id) else None
        )
        owner_name = _get_owner_name(owner_id, token) if owner_id else ""

        # 5. Resolve stage label
        raw_stage = deal_props.get("dealstage") or deal_info.get("deal_stage") or ""
        stage_label = _STAGE_LABEL_CACHE.get(raw_stage, raw_stage)

        # 6. Build output dict by walking COLUMN_MAP
        result: dict[str, str] = {}

        for col_letter, _header, source, prop_key, formatter in COLUMN_MAP:
            if source == "sdr":
                # SDR value is injected by sync.py — leave as placeholder
                result[col_letter] = ""
                continue

            if source == "deal":
                raw_value = deal_props.get(prop_key)
            elif source == "company":
                raw_value = company_props.get(prop_key)
            else:
                raw_value = None

            # Apply formatter
            if formatter == "owner_name":
                formatted = owner_name
            elif formatter == "stage_label":
                formatted = stage_label
            elif formatter == "passthrough":
                formatted = fmt_passthrough(raw_value)
            elif formatter == "capitalize":
                formatted = fmt_capitalize(raw_value)
            elif formatter == "number":
                formatted = fmt_number(raw_value)
            elif formatter == "date_dmy":
                formatted = fmt_date_dmy(raw_value)
            elif formatter == "month_year":
                formatted = fmt_month_year(raw_value)
            else:
                formatted = fmt_passthrough(raw_value)

            result[col_letter] = formatted

        return result

    except Exception as exc:
        logger.error("HubSpot get_row_data failed for domain %r: %s", domain, exc, exc_info=True)
        return None


# ── Cache management ──────────────────────────────────────────────────────────

def clear_cache() -> None:
    """Clear all process-lifetime caches (useful between test runs)."""
    global _AE_LOADED, _DOMAIN_DEAL_MAP
    _PIPELINE_CACHE.clear()
    _STAGE_LABEL_CACHE.clear()
    _AE_OWNER_IDS.clear()
    _AE_LOADED = False
    _OWNER_NAME_CACHE.clear()
    _DOMAIN_DEAL_MAP = None
