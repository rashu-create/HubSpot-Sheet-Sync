"""READ-ONLY preview of the "Source of meeting" column (col E since the 2026-10 move) before changes go live.

Resolves a source for every pipeline row exactly as the sync would (analysis Data tab first, then the
old Weekly Meetings sheet), compares it with what the source column (mapping.SOURCE_COL, E) holds now, and writes a CSV to review.
Nothing is written to any sheet or to HubSpot.

    FORCE_IPV4=1 python scripts/source_diff.py            # on this Mac IPv6 to Google hangs

Output: output/source_diff_<timestamp>.csv   (+ a summary on stdout)
"""

import csv
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if os.getenv("FORCE_IPV4") == "1":
    import urllib3.util.connection as _c
    _c.HAS_IPV6 = False

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src import meeting_source, sheets  # noqa: E402
from src.mapping import SOURCE_COL  # noqa: E402


def main() -> None:
    analysis = meeting_source.load_analysis()
    legacy = sheets.build_sdr_map(analysis.alias_map)
    domain_rows = sheets.read_pipeline_domains()
    first_meetings = sheets.read_pipeline_first_meetings()

    ws = (sheets._get_client()
          .open_by_key(os.environ["PIPELINE_SHEET_ID"].strip())
          .worksheet(os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026").strip()))
    current = {i + 1: (r[0] if r else "") for i, r in enumerate(ws.get(f"{SOURCE_COL}1:{SOURCE_COL}"))}

    sources = meeting_source.resolve_sources(
        [(row, d, first_meetings.get(row)) for row, d in domain_rows], analysis, legacy)

    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"source_diff_{datetime.now():%Y%m%d_%H%M}.csv"
    kinds: Counter = Counter()
    rules: Counter = Counter()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row", "domain", "canonical_domain", "first_meeting_B", "current_source", "new_source",
                    "rule", "change", "analysis_meetings (date:source[:rebooked])", "legacy_value"])
        for row, domain in domain_rows:
            canon = meeting_source.norm_domain(domain, analysis.alias_map) or ""
            new, rule = sources.get(row, (None, ""))
            kind = meeting_source.change_kind(current.get(row, ""), new)
            kinds[kind] += 1
            rules[rule or "unresolved"] += 1
            mine = sorted((m for m in analysis.meetings if m.dom == canon), key=lambda m: m.mtg)
            fm = first_meetings.get(row)
            w.writerow([
                row, domain, canon if canon != domain.strip().lower() else "",
                fm.isoformat() if fm else "", current.get(row, ""), new or "", rule, kind,
                " | ".join(f"{m.mtg.isoformat()}:{m.src}{':rebooked' if m.rebooking_of else ''}" for m in mine),
                legacy.get(canon, ""),
            ])

    print(f"Wrote {path}")
    print(f"Rows: {len(domain_rows)}   analysis meetings: {len(analysis.meetings)}   "
          f"aliases: {len(analysis.alias_map)}   alias problems: {len(analysis.alias_problems)}")
    for p in analysis.alias_problems:
        print("  alias problem:", p)
    print("By change:", dict(kinds))
    print("By rule:  ", dict(rules))


if __name__ == "__main__":
    main()
