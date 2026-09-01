# Employee Count Property Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Employee Count (col I) to read from the correct HubSpot property (`employee_count`) that the sales team actually fills, with `numberofemployees` as a fallback; also update the ICP size calculation to match.

**Architecture:** Two properties exist: `employee_count` (custom "R.Employee count", manually filled by sales team) and `numberofemployees` (standard HubSpot, auto-enriched, often blank). The fix adds `employee_count` to the fetched properties, computes a merged value with fallback in `get_row_data()`, and routes col I through that computed key. ICP size logic gets the same fallback.

**Tech Stack:** Python 3.11, FastAPI, gspread, HubSpot API v3; tests via pytest.

## Root Cause Summary

| Issue | Root Cause | Fix |
|---|---|---|
| "300" in Funding for sarvam.ai | HubSpot data entry error — `total_funding` has 300 (should be in `employee_count`). The sync is correct. | Manual HubSpot correction (no code change) |
| Employee count blank in sheet | Code reads `numberofemployees` (auto-enriched, usually blank); sales team fills `employee_count` (custom property) | Fetch `employee_count`, compute merged value with fallback |
| ICP Size miscalculated | Same — `_parse_int(company_props.get("numberofemployees"))` returns 0 when blank | Use `employee_count or numberofemployees` in ICP calc |

## Global Constraints

- Never write to column A (domain column).
- Only `mapping.py` and `hubspot.py` are touched — no API route or sheet changes.
- Keep `numberofemployees` in `COMPANY_PROPERTIES` — still needed as ICP size fallback.
- `fmt_number` is unchanged — the formatter itself is correct.
- All existing tests must stay green.

---

## Files Modified

| File | Change |
|---|---|
| `src/mapping.py` | Add `"employee_count"` to `COMPANY_PROPERTIES`; change col I COLUMN_MAP entry to `source="computed"`, `prop_key="employee_count_merged"` |
| `src/hubspot.py` | Add `employee_count_merged` computed key in `get_row_data()`; update ICP size fallback |
| `tests/test_mapping.py` | Add property presence test for `employee_count` |
| `tests/test_sync.py` | Add/update get_row_data tests for the merged employee count |

---

## Task 1: Update COMPANY_PROPERTIES and COLUMN_MAP

**Files:**
- Modify: `src/mapping.py`

**Interfaces:**
- Produces: `COMPANY_PROPERTIES` includes `"employee_count"`; col I COLUMN_MAP entry uses `source="computed"`, `prop_key="employee_count_merged"`

- [ ] **Step 1: Add `employee_count` to COMPANY_PROPERTIES**

In `src/mapping.py`, find the `COMPANY_PROPERTIES` list. Add `"employee_count"` right after `"total_funding"`. Keep `"numberofemployees"` — it is still needed as a fallback for ICP size.

Before:
```python
COMPANY_PROPERTIES = [
    "domain",
    "name",
    "hubspot_owner_id",
    "icp",
    "total_funding",
    "numberofemployees",
    "r__size_of_sales_team",
    ...
]
```

After:
```python
COMPANY_PROPERTIES = [
    "domain",
    "name",
    "hubspot_owner_id",
    "icp",
    "total_funding",
    "employee_count",       # custom "R.Employee count" — filled by sales team
    "numberofemployees",    # standard HubSpot auto-enriched — fallback only
    "r__size_of_sales_team",
    ...
]
```

- [ ] **Step 2: Update COLUMN_MAP col I to use computed source**

In `src/mapping.py`, change the col I entry from `"company"` source + `"numberofemployees"` to `"computed"` source + `"employee_count_merged"`:

Before:
```python
("I",  "Employee Count",  "company",   "numberofemployees",        "number"),
```

After:
```python
("I",  "Employee Count",  "computed",  "employee_count_merged",    "number"),
```

- [ ] **Step 3: Write the failing test**

In `tests/test_mapping.py`, add at the bottom:

```python
def test_employee_count_in_company_properties():
    """Custom employee_count property must be fetched on every company lookup."""
    from src.mapping import COMPANY_PROPERTIES
    assert "employee_count" in COMPANY_PROPERTIES


def test_col_i_uses_computed_employee_count_merged():
    """Col I must use computed source so the fallback logic in hubspot.py applies."""
    from src.mapping import COLUMN_MAP
    cols = {entry[0]: entry for entry in COLUMN_MAP}
    assert "I" in cols, "Col I missing from COLUMN_MAP"
    col, header, source, prop, formatter = cols["I"]
    assert source == "computed", f"Col I source should be 'computed', got {source!r}"
    assert prop == "employee_count_merged"
    assert formatter == "number"
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd ~/hubspot-sheet-sync && python -m pytest tests/test_mapping.py::test_employee_count_in_company_properties tests/test_mapping.py::test_col_i_uses_computed_employee_count_merged -v
```

Expected: FAIL — `employee_count` not yet in list, col I source still `"company"`.

- [ ] **Step 5: Apply the two changes to mapping.py (Steps 1 + 2 above)**

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd ~/hubspot-sheet-sync && python -m pytest tests/test_mapping.py -v
```

Expected: all pass, including the two new tests.

- [ ] **Step 7: Commit**

```bash
git add src/mapping.py tests/test_mapping.py
git commit -m "fix: read employee_count (custom) for col I, with numberofemployees as fallback"
```

---

## Task 2: Add employee_count_merged computed key in get_row_data() and fix ICP size

**Files:**
- Modify: `src/hubspot.py` (lines ~825–835, inside `get_row_data()`)

**Interfaces:**
- Consumes: `company_props` dict (already fetched, now includes `"employee_count"` and `"numberofemployees"`)
- Produces: `computed["employee_count_merged"]` — string; `computed["icp_size"]` — uses merged employee count

- [ ] **Step 1: Write the failing tests**

In `tests/test_sync.py`, add or update a test for `get_row_data()` mock that verifies the merge logic. Add this test class:

```python
class TestEmployeeCountMerge:
    """get_row_data correctly merges employee_count → numberofemployees for col I."""

    def _make_company_props(self, employee_count=None, numberofemployees=None):
        return {
            "employee_count": employee_count,
            "numberofemployees": numberofemployees,
            "total_funding": "10000000",
            "icp": "open source",
            "r__size_of_sales_team": "3",
            "l1_qualified__": "yes",
            "l2_qualified__": "yes",
            "l3_qualified____cloned_": "maybe",
            "r_l1_qualification_comments_form": "",
            "r_l2_qualification_comments_form": "CTO",
            "r_l3_qualification_comments_form": "",
            "next_steps": "",
            "next_steps_due_date": "",
            "notes_from_call": "",
            "domain": "example.com",
            "name": "Example Corp",
            "hubspot_owner_id": "",
        }

    def test_employee_count_primary(self):
        """employee_count is used when set, even if numberofemployees is also set."""
        from src.hubspot import _compute_employee_count_merged
        props = self._make_company_props(employee_count="300", numberofemployees="500")
        assert _compute_employee_count_merged(props) == "300"

    def test_numberofemployees_fallback(self):
        """numberofemployees is used when employee_count is None/empty."""
        from src.hubspot import _compute_employee_count_merged
        props = self._make_company_props(employee_count=None, numberofemployees="500")
        assert _compute_employee_count_merged(props) == "500"

    def test_both_empty_returns_empty(self):
        """Both None → empty string."""
        from src.hubspot import _compute_employee_count_merged
        props = self._make_company_props(employee_count=None, numberofemployees=None)
        assert _compute_employee_count_merged(props) == ""

    def test_icp_size_uses_employee_count_over_numberofemployees(self):
        """ICP size is computed from employee_count when set."""
        from src.hubspot import _compute_icp_size
        # employee_count=600 → Enterprise, even if numberofemployees is 50
        assert _compute_icp_size(employee_count="600", numberofemployees="50", sales_team="0") == "Enterprise"

    def test_icp_size_falls_back_to_numberofemployees(self):
        """ICP size falls back to numberofemployees when employee_count is blank."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size(employee_count=None, numberofemployees="300", sales_team="0") == "Commercial"

    def test_icp_size_startup_when_both_blank(self):
        """Both blank → 0 employees → Startup (unless sales_team >= 2 and <200 emp)."""
        from src.hubspot import _compute_icp_size
        assert _compute_icp_size(employee_count=None, numberofemployees=None, sales_team="0") == "Startup"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/hubspot-sheet-sync && python -m pytest tests/test_sync.py::TestEmployeeCountMerge -v
```

Expected: FAIL — `_compute_employee_count_merged` and `_compute_icp_size` not yet defined as standalone functions.

- [ ] **Step 3: Extract and add the two helper functions in `src/hubspot.py`**

In `src/hubspot.py`, find the section starting at line ~820 (inside `get_row_data()`) where ICP size is computed:

```python
# ICP Size: Enterprise / Commercial / SMB / Startup
employees = _parse_int(company_props.get("numberofemployees"))
sales_team = _parse_int(company_props.get("r__size_of_sales_team"))
if employees >= 500:
    computed["icp_size"] = "Enterprise"
elif employees >= 200:
    computed["icp_size"] = "Commercial"
elif employees < 200 and sales_team >= 2:
    computed["icp_size"] = "SMB"
else:
    computed["icp_size"] = "Startup"
```

**Before the `get_row_data` function** (in the module scope, near the other helpers around line 665), add:

```python
def _compute_employee_count_merged(company_props: dict) -> str:
    """Return employee_count if set, else numberofemployees, else ''.

    employee_count is the custom 'R.Employee count' property filled by
    the sales team. numberofemployees is the standard HubSpot auto-enriched
    property that is often blank for developer-tool companies.
    """
    from src.mapping import fmt_passthrough
    val = fmt_passthrough(company_props.get("employee_count"))
    if not val:
        val = fmt_passthrough(company_props.get("numberofemployees"))
    return val


def _compute_icp_size(
    employee_count: str | None,
    numberofemployees: str | None,
    sales_team: str | None,
) -> str:
    """Return ICP size bucket from employee count with fallback.

    Priority: employee_count (custom) → numberofemployees (auto-enriched).
    Thresholds: Enterprise ≥500, Commercial ≥200, SMB <200 & sales_team ≥2, else Startup.
    """
    employees = _parse_int(employee_count or numberofemployees)
    sales = _parse_int(sales_team)
    if employees >= 500:
        return "Enterprise"
    elif employees >= 200:
        return "Commercial"
    elif employees < 200 and sales >= 2:
        return "SMB"
    else:
        return "Startup"
```

- [ ] **Step 4: Update `get_row_data()` to use the new helpers**

Find the ICP size block inside `get_row_data()` and replace it:

Before:
```python
# ICP Size: Enterprise / Commercial / SMB / Startup
employees = _parse_int(company_props.get("numberofemployees"))
sales_team = _parse_int(company_props.get("r__size_of_sales_team"))
if employees >= 500:
    computed["icp_size"] = "Enterprise"
elif employees >= 200:
    computed["icp_size"] = "Commercial"
elif employees < 200 and sales_team >= 2:
    computed["icp_size"] = "SMB"
else:
    computed["icp_size"] = "Startup"
```

After:
```python
# ICP Size: Enterprise / Commercial / SMB / Startup
computed["icp_size"] = _compute_icp_size(
    employee_count=company_props.get("employee_count"),
    numberofemployees=company_props.get("numberofemployees"),
    sales_team=company_props.get("r__size_of_sales_team"),
)

# Employee Count: custom property first, standard auto-enriched as fallback
computed["employee_count_merged"] = _compute_employee_count_merged(company_props)
```

Note: Remove the old `employees` and `sales_team` local variables — they are replaced by the helpers.

- [ ] **Step 5: Fix the circular import in `_compute_employee_count_merged`**

The helper uses `fmt_passthrough` from `mapping.py`. But `hubspot.py` already imports from `mapping.py` at the top:

```python
from src.mapping import (
    COLUMN_MAP,
    COMPANY_PROPERTIES,
    DEAL_PROPERTIES,
    apply_formatter,
    normalize_domain,
)
```

Add `fmt_passthrough` to that import:

```python
from src.mapping import (
    COLUMN_MAP,
    COMPANY_PROPERTIES,
    DEAL_PROPERTIES,
    apply_formatter,
    fmt_passthrough,
    normalize_domain,
)
```

Then simplify the helper to remove the inline import:

```python
def _compute_employee_count_merged(company_props: dict) -> str:
    """Return employee_count if set, else numberofemployees, else ''."""
    val = fmt_passthrough(company_props.get("employee_count"))
    if not val:
        val = fmt_passthrough(company_props.get("numberofemployees"))
    return val
```

- [ ] **Step 6: Run tests**

```bash
cd ~/hubspot-sheet-sync && python -m pytest tests/ -v
```

Expected: all pass, including the six new TestEmployeeCountMerge tests.

- [ ] **Step 7: Commit**

```bash
git add src/hubspot.py tests/test_sync.py
git commit -m "fix: use employee_count (custom) with numberofemployees fallback for ICP size and col I"
```

---

## Task 3: Run smoke test and verify the fix live

No code changes — validation step.

- [ ] **Step 1: Run the full test suite**

```bash
cd ~/hubspot-sheet-sync && python -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 2: Run smoke test for sarvam.ai specifically**

Temporarily edit `scripts/smoke_test.py` to test sarvam.ai — set LIMIT to the row index for sarvam, or just check its output manually:

```bash
cd ~/hubspot-sheet-sync && python scripts/smoke_test.py 2>&1 | grep -A 20 "sarvam"
```

Check:
- Col H (Funding): should show the value from HubSpot `total_funding` for sarvam (will still show 300 — this is a HubSpot data issue, not a code bug)
- Col I (Employee Count): should now show sarvam's `employee_count` value if set

- [ ] **Step 3: Trigger a manual dry-run sync via the dashboard**

```
POST https://hubspot-sheet-sync.data.reo.dev/api/sync?dry_run=true
```

Check the logs for any errors. Confirm col I is now populated for companies that have `employee_count` set.

- [ ] **Step 4: HubSpot data fix for sarvam.ai (manual, not code)**

In HubSpot, find the sarvam.ai company record:
- Clear or correct the `total_funding` field (it has "300" which is the employee count, not funding)
- Confirm `employee_count` has the correct value (300)

After the HubSpot fix, the next sync will show blank (or correct value) in Funding and "300" in Employee Count for sarvam.ai.

- [ ] **Step 5: Deploy**

```bash
cd ~/hubspot-sheet-sync && bash deploy/deploy.sh
```

- [ ] **Step 6: Trigger a live sync**

```
POST https://hubspot-sheet-sync.data.reo.dev/api/sync
```

Verify in the Google Sheet that Employee Count (col I) is now populated for companies where the sales team has filled in `employee_count` in HubSpot.
