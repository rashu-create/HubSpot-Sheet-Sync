"""get_row_data exposes what the Next Steps write-back needs, as underscore-prefixed meta keys (never written)."""
from unittest.mock import patch

import pytest

from src import hubspot

DEAL_MAP = {"acme.com": {"deal_id": "D1", "company_id": "C1", "owner_id": None, "deal_stage": "s1", "createdate": ""}}


@pytest.fixture
def lookup(monkeypatch):
    monkeypatch.setenv("HUBSPOT_API_TOKEN", "tok")

    def run(deal_props, company_props):
        with patch.object(hubspot, "_DOMAIN_DEAL_MAP", DEAL_MAP), \
             patch.object(hubspot, "_get_sales_pipeline_id", return_value="p1"), \
             patch.object(hubspot, "fetch_deal_properties", return_value=deal_props), \
             patch.object(hubspot, "fetch_company_properties", return_value=company_props), \
             patch.object(hubspot, "_compute_owner_first_names", return_value="Akanksha"):
            return hubspot.get_row_data("acme.com")
    return run


def test_meta_has_deal_id_name_and_the_deal_values(lookup):
    r = lookup({"dealname": "Acme - Reo", "next_steps_management": "Call Bob",
                "next_steps_due_date_management": "2026-10-25", "dealstage": "s1"}, {})
    assert (r["_deal_id"], r["_deal_name"]) == ("D1", "Acme - Reo")
    assert (r["_hs_s"], r["_hs_t"]) == ("Call Bob", "2026-10-25")
    assert r["S"] == "Call Bob" and r["T"] == "25 Oct 2026"          # what is written to the sheet is unchanged


def test_meta_uses_the_same_company_fallback_as_the_sheet_cells(lookup):
    r = lookup({"dealname": "Acme", "dealstage": "s1"},
               {"next_steps": "From company", "next_steps_due_date": "2026-11-02"})
    assert (r["_hs_s"], r["_hs_t"]) == ("From company", "2026-11-02")
    assert r["S"] == "From company" and r["T"] == "2 Nov 2026"


def test_meta_keys_are_all_underscore_prefixed(lookup):
    r = lookup({"dealname": "Acme", "dealstage": "s1"}, {})
    from src.mapping import COLUMN_MAP
    letters = {e[0] for e in COLUMN_MAP}
    assert {k for k in r if k not in letters} == {"_deal_id", "_deal_name", "_hs_s", "_hs_t"}
