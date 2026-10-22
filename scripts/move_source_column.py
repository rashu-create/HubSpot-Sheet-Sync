"""One-shot: move "Source of meeting (Muni Sheet)" from col AG to col E in the pipeline tab (2026-10 cutover).

    FORCE_IPV4=1 python scripts/move_source_column.py              # --check: read-only, says which layout the tab has
    FORCE_IPV4=1 python scripts/move_source_column.py --apply --yes   # backup tab -> ONE moveDimension -> verify every cell
    FORCE_IPV4=1 python scripts/move_source_column.py --rollback --yes  # E back to AG, verified against the backup

Run ONLY as part of the cutover in ~/.claude/plans/cheeky-roaming-hoare.md (Release 2): right after a sync, with
hubspot-sheet-sync and ae-kpi-tracker deployments ready — both refuse to write while the layout does not match their
code (header guards), but the Weekly tracker and analysis formulas only stay right if both are deployed straight away.
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if os.getenv("FORCE_IPV4") == "1":
    import urllib3.util.connection as _c
    _c.HAS_IPV6 = False

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src import sheets  # noqa: E402
from src.column_move import (  # noqa: E402
    diff_rows, is_post_move, is_pre_move, move_request, rollback_request, verify_move,
)


def _open():
    tab = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    ss = sheets._get_client().open_by_key(os.environ["PIPELINE_SHEET_ID"].strip())
    return ss, ss.worksheet(tab), tab


def _layout(header: list) -> str:
    return "PRE-move (Source in AG)" if is_pre_move(header) else "POST-move (Source in E)" if is_post_move(header) \
        else "UNKNOWN (neither layout) — do not move anything"


def _cf_formulas(ss, ws) -> list[str]:
    """Custom-formula conditional-format rules on the tab, to eyeball that references travelled with the move."""
    meta = ss.fetch_sheet_metadata({"fields": "sheets(properties.sheetId,conditionalFormats)"})
    out = []
    for sh in meta["sheets"]:
        if sh["properties"]["sheetId"] == ws.id:
            for rule in sh.get("conditionalFormats", []):
                for v in rule.get("booleanRule", {}).get("condition", {}).get("values", []):
                    if "userEnteredValue" in v:
                        out.append(v["userEnteredValue"])
    return out


def _backup_title(tab: str) -> str:
    return f"{tab} – backup {date.today():%Y-%m-%d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required with --apply / --rollback")
    args = ap.parse_args()

    ss, ws, tab = _open()
    header = ws.row_values(1)
    print(f"Tab {tab!r}: layout is {_layout(header)}")

    if not (args.apply or args.rollback):
        print("Read-only check only. Nothing changed.")
        return 0
    if not args.yes:
        print("Refusing without --yes.")
        return 2

    backup_title = _backup_title(tab)
    if args.apply:
        if not is_pre_move(header):
            print("Not in the PRE-move layout — refusing.")
            return 2
        if any(w.title == backup_title for w in ss.worksheets()):
            print(f"A tab named {backup_title!r} already exists — refusing (rename or delete it first).")
            return 2
        old = ws.get_all_values()
        print(f"Read {len(old)} rows. Duplicating the tab as a hidden backup {backup_title!r} ...")
        backup = ss.duplicate_sheet(ws.id, new_sheet_name=backup_title)
        ss.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": backup.id, "hidden": True}, "fields": "hidden"}}]})
        print("Moving column AG -> E ...")
        ss.batch_update({"requests": [move_request(ws.id)]})
        problems = verify_move(old, ws.get_all_values())
        print("Custom-formula conditional formats now:", _cf_formulas(ss, ws))
        if problems:
            print("VERIFY FAILED:\n  " + "\n  ".join(problems))
            print("Roll back with:  python scripts/move_source_column.py --rollback --yes")
            return 1
        print(f"VERIFIED: all {len(old)} rows match the backup with the move applied. Backup tab kept (hidden).")
        return 0

    # rollback
    if not is_post_move(header):
        print("Not in the POST-move layout — refusing.")
        return 2
    backup_ws = next((w for w in ss.worksheets() if w.title.startswith(f"{tab} – backup ")), None)
    if backup_ws is None:
        print("No backup tab found — refusing to roll back blind.")
        return 2
    print(f"Moving column E -> AG; will compare with {backup_ws.title!r} ...")
    ss.batch_update({"requests": [rollback_request(ws.id)]})
    problems = diff_rows(backup_ws.get_all_values(), ws.get_all_values())
    if problems:
        print("ROLLBACK DIFFERS FROM BACKUP (data may have changed since the backup, e.g. a sync ran):\n  "
              + "\n  ".join(problems[:20]))
        return 1
    print("ROLLED BACK: the tab equals the backup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
