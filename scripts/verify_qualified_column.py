"""
One-shot verification script for the Qualified column insertion.
"""

import os
import sys

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

CREDS_FILE = os.environ["GOOGLE_CREDENTIALS_FILE"]
SHEET_ID = os.environ["PIPELINE_SHEET_ID"]
TAB_NAME = os.environ["PIPELINE_TAB_NAME"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

KNOWN_MISSES = {"namespace.so", "kilo.ai", "corellium.com", "amd.com", "usegit.ai", "rtk-ai.app"}


def col_letter_to_index(letter: str) -> int:
    letter = letter.upper()
    result = 0
    for ch in letter:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


def parse_funding(raw: str) -> float:
    if not raw:
        return 0.0
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def parse_int(raw: str) -> int:
    if not raw:
        return 0
    try:
        return int(float(str(raw).replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


def main():
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(TAB_NAME)

    print(f"Opened: {sh.title} / {ws.title}")
    print(f"Sheet dimensions: {ws.row_count} rows x {ws.col_count} cols\n")

    # Fetch A1:AQ500 to see one column beyond AP too
    all_values = ws.get("A1:AQ500")

    header = all_values[0]

    def get_header(col_letter: str) -> str:
        idx = col_letter_to_index(col_letter) - 1
        return header[idx] if idx < len(header) else "(missing)"

    checks_passed = 0
    checks_failed = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal checks_passed, checks_failed
        sym = "✓" if condition else "✗ FAIL:"
        print(f"  {sym} {label}" + (f" — {detail}" if detail else ""))
        if condition:
            checks_passed += 1
        else:
            checks_failed += 1

    # ── Column indices ───────────────────────────────────────────────────────
    a_idx  = col_letter_to_index("A")  - 1
    h_idx  = col_letter_to_index("H")  - 1   # funding
    j_idx  = col_letter_to_index("J")  - 1   # sales team size
    ai_idx = col_letter_to_index("AI") - 1
    aj_idx = col_letter_to_index("AJ") - 1
    ak_idx = col_letter_to_index("AK") - 1
    al_idx = col_letter_to_index("AL") - 1
    am_idx = col_letter_to_index("AM") - 1
    an_idx = col_letter_to_index("AN") - 1
    ao_idx = col_letter_to_index("AO") - 1
    ap_idx = col_letter_to_index("AP") - 1

    # ── SECTION 1: Header checks ─────────────────────────────────────────────
    print("=== 1. Header checks ===")
    check("AI1 == 'Qualified'",        get_header("AI") == "Qualified",                  f"got: '{get_header('AI')}'")
    check("AJ1 == 'Closure Month'",    get_header("AJ") == "Closure Month",              f"got: '{get_header('AJ')}'")
    check("AK1 == 'Opportunity loss reason'", get_header("AK") == "Opportunity loss reason", f"got: '{get_header('AK')}'")
    check("AL1 == 'Opportunity loss reason - deepdive'", get_header("AL") == "Opportunity loss reason - deepdive", f"got: '{get_header('AL')}'")
    check("AO1 has 'Deal' in header (amount/value)", "deal" in get_header("AO").lower(), f"got: '{get_header('AO')}'")

    ap_header = get_header("AP")
    check(
        "AP1 == 'Is First?' (DEDUP header)",
        ap_header == "Is First?",
        f"got: '{ap_header}'",
    )
    print(f"  → Note: AM='{get_header('AM')}' | AN='{get_header('AN')}' (should be manual/SKIP cols)")

    # ── Collect data rows ────────────────────────────────────────────────────
    data_rows = []
    for row in all_values[1:]:
        domain = row[a_idx] if a_idx < len(row) else ""
        if not domain:
            break
        def cell(idx):
            return row[idx] if idx < len(row) else ""
        data_rows.append({
            "domain": domain,
            "qualified": cell(ai_idx),
            "funding_raw": cell(h_idx),
            "sales_raw": cell(j_idx),
            "aj_val": cell(aj_idx),
            "ak_val": cell(ak_idx),
            "al_val": cell(al_idx),
            "am_val": cell(am_idx),
            "an_val": cell(an_idx),
            "deal_amount": cell(ao_idx),
            "ap_val": cell(ap_idx),
            "row_num": all_values.index(row) + 1,
        })

    total = len(data_rows)
    syncable = [r for r in data_rows if r["domain"] not in KNOWN_MISSES]
    misses   = [r for r in data_rows if r["domain"] in KNOWN_MISSES]

    print(f"\nData rows: {total} total | {len(syncable)} syncable | {len(misses)} known misses")

    # ── SECTION 2: Qualified values ──────────────────────────────────────────
    print("\n=== 2. Qualified column (AI) values ===")

    yes_count = sum(1 for r in syncable if r["qualified"] == "Yes")
    no_count  = sum(1 for r in syncable if r["qualified"] == "No")
    blank_syncable = [r for r in syncable if r["qualified"] not in ("Yes", "No")]
    miss_blanks    = [r for r in misses if r["qualified"] == ""]

    check(
        "All syncable rows have 'Yes' or 'No' (no blanks)",
        len(blank_syncable) == 0,
        f"{len(blank_syncable)} bad: {[r['domain'] for r in blank_syncable[:5]]}" if blank_syncable else "",
    )
    check(
        "Known-miss rows are blank (sync skips them — expected)",
        all(r["qualified"] == "" for r in misses),
        f"{len(miss_blanks)}/{len(misses)} blank" if misses else "no misses",
    )
    print(f"  → Syncable: Yes={yes_count} | No={no_count}")

    # ── SECTION 3: Logic cross-check ─────────────────────────────────────────
    print("\n=== 3. Qualified logic cross-check ===")
    logic_errors = []
    for r in syncable:
        funding = parse_funding(r["funding_raw"])
        sales   = parse_int(r["sales_raw"])
        expected = "Yes" if (sales > 0 or funding > 5_000_000) else "No"
        if r["qualified"] != expected:
            logic_errors.append({**r, "expected": expected, "funding": funding, "sales": sales})

    check(
        "Logic correct: sales>0 OR funding>5M → Yes, else No",
        len(logic_errors) == 0,
        f"{len(logic_errors)} mismatches" if logic_errors else "",
    )
    if logic_errors:
        print("  Mismatches:")
        for e in logic_errors[:10]:
            print(f"    row {e['row_num']:3d} {e['domain']:<35} funding={e['funding']:>12,.0f}  sales={e['sales']:>4}  expected={e['expected']}  got='{e['qualified']}'")

    # ── SECTION 4: Shifted columns integrity ────────────────────────────────
    print("\n=== 4. Shifted column integrity ===")

    # AJ: Closure Month — should have data (month names / dates), NOT Yes/No
    aj_leaked_qi = sum(1 for r in data_rows if r["aj_val"] in ("Yes", "No"))
    aj_non_blank  = sum(1 for r in data_rows if r["aj_val"])
    check("AJ has no 'Yes'/'No' leak (shift correct)", aj_leaked_qi == 0, f"{aj_leaked_qi} rows leaked")
    print(f"  → AJ non-blank: {aj_non_blank}/{total}")

    # AK/AL: shouldn't be full of "Yes"/"No"
    ak_leaked = sum(1 for r in data_rows if r["ak_val"] in ("Yes", "No") and r["ak_val"])
    check("AK has no 'Yes'/'No' leak", ak_leaked == 0, f"{ak_leaked} rows")

    # AM/AN: these were the manual SKIP cols — should not have Qualified data
    am_qi = sum(1 for r in data_rows if r["am_val"] in ("Yes", "No"))
    an_qi = sum(1 for r in data_rows if r["an_val"] in ("Yes", "No"))
    check("AM has no 'Yes'/'No' (not Qualified data)", am_qi == 0, f"{am_qi} rows")
    check("AN has no 'Yes'/'No' (not Qualified data)", an_qi == 0, f"{an_qi} rows")

    # AO: Deal Amount — numeric-ish values
    ao_non_blank = sum(1 for r in data_rows if r["deal_amount"])
    ao_qi = sum(1 for r in data_rows if r["deal_amount"] in ("Yes", "No"))
    check("AO has no 'Yes'/'No' (correct — deal amounts)", ao_qi == 0, f"{ao_qi} rows")
    print(f"  → AO (Deal Amount) non-blank: {ao_non_blank}/{total}")

    # Sample AO values (to check they look like numbers)
    ao_samples = [r["deal_amount"] for r in data_rows if r["deal_amount"]][:8]
    print(f"  → AO sample values: {ao_samples}")

    # ── SECTION 5: DEDUP (AP) ────────────────────────────────────────────────
    print("\n=== 5. DEDUP column (AP) ===")

    ap_formula_cell = ws.acell("AP2", value_render_option="FORMULA").value
    print(f"  AP2 formula: {ap_formula_cell[:80] if ap_formula_cell else '(blank)'}")

    ap_ones   = sum(1 for r in data_rows if r["ap_val"] == "1")
    ap_zeros  = sum(1 for r in data_rows if r["ap_val"] == "0")
    ap_blanks = sum(1 for r in data_rows if r["ap_val"] == "")
    ap_other  = sum(1 for r in data_rows if r["ap_val"] not in ("0", "1", ""))
    print(f"  → 1s={ap_ones} | 0s={ap_zeros} | blank={ap_blanks} | other={ap_other}")

    # Sample blanks
    blank_ap_rows = [r for r in data_rows if r["ap_val"] == ""][:5]
    if blank_ap_rows:
        print(f"  → Sample blank AP rows: {[(r['row_num'], r['domain']) for r in blank_ap_rows]}")

    check(
        "AP DEDUP has formula in AP2",
        ap_formula_cell and "ARRAYFORMULA" in str(ap_formula_cell).upper(),
        f"formula='{str(ap_formula_cell)[:60]}'" if ap_formula_cell else "blank",
    )
    check(
        "AP DEDUP covers all syncable data rows (1s + 0s == syncable count)",
        ap_ones + ap_zeros == len(syncable),
        f"1s={ap_ones} + 0s={ap_zeros} = {ap_ones+ap_zeros} vs {len(syncable)} syncable (known misses excluded)",
    )
    if ap_blanks > 0:
        blank_domains = {r["domain"] for r in data_rows if r["ap_val"] == ""}
        overlap_misses = blank_domains & KNOWN_MISSES
        print(f"  → Blank AP rows: {ap_blanks} | overlap with known misses: {len(overlap_misses)}")

    # ── SECTION 6: Sample rows ───────────────────────────────────────────────
    print("\n=== 6. Sample rows ===")
    print("Yes (first 5 syncable):")
    for r in [r for r in syncable if r["qualified"] == "Yes"][:5]:
        print(f"  row {r['row_num']:3d} | {r['domain']:<35} | fund={r['funding_raw']:<12} | sales={r['sales_raw']}")
    print("No (first 5 syncable):")
    for r in [r for r in syncable if r["qualified"] == "No"][:5]:
        print(f"  row {r['row_num']:3d} | {r['domain']:<35} | fund={r['funding_raw']:<12} | sales={r['sales_raw']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"RESULT: {checks_passed} passed, {checks_failed} failed")
    if checks_failed == 0:
        print("ALL CHECKS PASSED ✓")
    else:
        print("SOME CHECKS FAILED — see above")
        sys.exit(1)


if __name__ == "__main__":
    main()
