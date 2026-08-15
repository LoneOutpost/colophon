"""Acceptance gate for core.known_entity: over a colophon DB, build the known-series set from every
book that HAS a series, then for each series-LESS book run match_known_series with the same exclusion
the pipeline uses (resolved title + authors) and report how many would gain a series. Prints examples
so false hits (a title/author token misread as a series) are visible. Usage:
    uv run python scripts/known_series_dryrun.py [DB]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from colophon.core.known_entity import build_known_series, match_known_series
from colophon.core.metadata_quality import is_junk_title

db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/colophon.db"
con = sqlite3.connect(db)

rows = [json.loads(d) for (d,) in con.execute("SELECT data FROM book_units")]
series_names = [s["name"] for u in rows for s in (u.get("series") or []) if s.get("name")]
known = build_known_series(series_names)
print(f"DB {db}")
print(f"known series (junk-filtered): {len(known)} distinct  (from {len(series_names)} series refs)")

gained = 0
examples: list = []
for u in rows:
    if u.get("series"):
        continue
    title = (u.get("title") or "").strip()
    if title and is_junk_title(title):        # broken identity -> don't attach a series
        continue
    authors = u.get("authors") or []
    cands = [Path(u["source_folder"]).name]
    for sf in u.get("source_files", [])[:3]:
        cands.append(Path(sf["path"]).stem)
        alb = (sf.get("tags") or {}).get("album")
        if alb:
            cands.append(alb)
    name = match_known_series(cands, known, exclude=[title, *authors])
    if name:
        gained += 1
        if len(examples) < 40:
            examples.append((Path(u["source_folder"]).name, title, authors, name))

print(f"series-less books that would GAIN a series: {gained}")
print("\n== examples (folder | title | authors -> series) ==")
for folder, title, authors, name in examples:
    print(f"   {folder[:48]!r:50} t={title[:28]!r:30} a={authors} -> {name!r}")
