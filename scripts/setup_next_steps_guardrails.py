"""One-shot: sheet-side guardrails for the Next Steps / Due Date write-back.

  * Due Date column: "must be a valid date" (rejects typos like "soon"), data rows only.
  * Header notes on Next Steps and Due Date: "Edits here sync to HubSpot at 10 AM / 10 PM IST ...".

Only adds a validation rule and two notes. It never touches conditional formatting or the Owner chips (unlike
scripts/setup_sheet.py, which resets them). Safe to re-run.

    FORCE_IPV4=1 python scripts/setup_next_steps_guardrails.py           # prints what it would do
    FORCE_IPV4=1 python scripts/setup_next_steps_guardrails.py --apply
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if os.getenv("FORCE_IPV4") == "1":
    import urllib3.util.connection as _c
    _c.HAS_IPV6 = False

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.mapping import DUE_DATE_COL, NEXT_STEPS_COL, col_letter_to_index  # noqa: E402

_DATA_END_ROW = 600     # 0-based exclusive, same extent as scripts/setup_sheet.py

_NOTE_S = ("Edits here sync to HubSpot (deal 'Next Steps (Management)') at 10 AM and 10 PM IST.\n"
           "Rules: the cell can't be left empty (it is restored from HubSpot); if HubSpot changed too, HubSpot wins.")
_NOTE_T = ("Edits here sync to HubSpot (deal 'Next Steps Due Date (Management)') at 10 AM and 10 PM IST.\n"
           "Must be a real date, from 30 days ago to 1 year ahead; can't be left empty; if HubSpot changed too, HubSpot wins.")


def build_requests(sheet_id: int) -> list[dict]:
    """Sheets API requests: strict date validation on the Due Date column + a note on both header cells."""
    s0 = col_letter_to_index(NEXT_STEPS_COL) - 1
    t0 = col_letter_to_index(DUE_DATE_COL) - 1
    note = lambda col, text: {"updateCells": {                       # noqa: E731
        "rows": [{"values": [{"note": text}]}],
        "fields": "note",
        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": col},
    }}
    return [
        {"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": _DATA_END_ROW,
                      "startColumnIndex": t0, "endColumnIndex": t0 + 1},
            "rule": {"condition": {"type": "DATE_IS_VALID"},
                     "inputMessage": "Enter a date. Syncs to HubSpot at 10 AM / 10 PM IST.",
                     "strict": True, "showCustomUi": False},
        }},
        note(s0, _NOTE_S),
        note(t0, _NOTE_T),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from src import sheets
    tab = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    ss = sheets._get_client().open_by_key(os.environ["PIPELINE_SHEET_ID"].strip())
    ws = ss.worksheet(tab)
    header = ws.row_values(1)
    for col, want in ((NEXT_STEPS_COL, "Next Steps"), (DUE_DATE_COL, "Due Date")):
        have = header[col_letter_to_index(col) - 1] if len(header) >= col_letter_to_index(col) else ""
        if have.strip().lower() != want.lower():
            print(f"Column {col} is {have!r}, expected {want!r} — wrong layout, refusing.")
            return 2
    reqs = build_requests(ws.id)
    print(f"{len(reqs)} request(s) for {tab!r}: date validation on {DUE_DATE_COL}2:{DUE_DATE_COL}{_DATA_END_ROW}, "
          f"notes on {NEXT_STEPS_COL}1 and {DUE_DATE_COL}1.")
    if not args.apply:
        print("Dry: nothing changed. Re-run with --apply.")
        return 0
    ss.batch_update({"requests": reqs})
    print("Applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
