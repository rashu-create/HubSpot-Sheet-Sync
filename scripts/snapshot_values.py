"""READ-ONLY: snapshot the values of every tab except the pipeline tab, and diff two snapshots.

Used around the Source-of-meeting column move: the analysis tabs and the Weekly tracker are formulas over the
pipeline tab, so after the move (Sheets rewrites the references) their VALUES must be identical. This check does not
depend on any column letter in any service's code.

    FORCE_IPV4=1 python scripts/snapshot_values.py --out /tmp/baseline.json
    FORCE_IPV4=1 python scripts/snapshot_values.py --compare /tmp/baseline.json     # exit 1 if anything differs

Skipped: the pipeline tab itself (it is what moves), hidden backup tabs ("... backup ..."), "ZZ" scratch tabs.
"""
import argparse
import json
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

from gspread.utils import rowcol_to_a1  # noqa: E402


def _trim(rows: list[list]) -> list[list]:
    """Drop trailing empty cells and trailing empty rows (Sheets reads trim them inconsistently)."""
    out = []
    for r in rows:
        r = [str(c) for c in r]
        while r and r[-1] == "":
            r.pop()
        out.append(r)
    while out and not out[-1]:
        out.pop()
    return out


def diff(old: dict, new: dict, limit: int = 40) -> list[str]:
    """Human-readable differences between two {tab: rows} snapshots (empty list = identical)."""
    problems: list[str] = []
    for tab in sorted(set(old) | set(new)):
        if tab not in new:
            problems.append(f"tab missing now: {tab!r}")
        elif tab not in old:
            problems.append(f"new tab: {tab!r}")
        else:
            a, b = _trim(old[tab]), _trim(new[tab])
            for r in range(max(len(a), len(b))):
                ra, rb = (a[r] if r < len(a) else []), (b[r] if r < len(b) else [])
                for c in range(max(len(ra), len(rb))):
                    va, vb = (ra[c] if c < len(ra) else ""), (rb[c] if c < len(rb) else "")
                    if va != vb:
                        problems.append(f"{tab!r} {rowcol_to_a1(r + 1, c + 1)}: {va!r} -> {vb!r}")
                        if len(problems) >= limit:
                            problems.append(f"... (stopped at the first {limit})")
                            return problems
    return problems


def dump() -> dict[str, list[list[str]]]:
    from src import sheets
    pipeline = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()
    ss = sheets._get_client().open_by_key(os.environ["PIPELINE_SHEET_ID"].strip())
    snap = {}
    for ws in ss.worksheets():
        if ws.title == pipeline or "backup" in ws.title.lower() or ws.title.startswith("ZZ"):
            continue
        snap[ws.title] = _trim(ws.get_all_values())
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out")
    g.add_argument("--compare")
    args = ap.parse_args()
    now = dump()
    if args.out:
        Path(args.out).write_text(json.dumps(now))
        print(f"Saved {len(now)} tabs, {sum(len(v) for v in now.values())} rows -> {args.out}")
        return 0
    problems = diff(json.loads(Path(args.compare).read_text()), now)
    if problems:
        print(f"{len(problems)} difference(s):\n  " + "\n  ".join(problems))
        return 1
    print(f"IDENTICAL: {len(now)} tabs match the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
