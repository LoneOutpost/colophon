"""Classify each known-series match: does it come from a MIDDLE `.-.` segment (the series slot, trust)
or ONLY from the LAST segment / a file stem (the title slot, suspect)? In an `Author.-.[Series].-.Title`
leaf the title is last, so a match that ONLY the last segment produces is a title misread as a series."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from colophon.core.known_entity import build_known_series, match_known_series

db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/colophon.db"
con = sqlite3.connect(db)
rows = [json.loads(d) for (d,) in con.execute("SELECT data FROM book_units")]
known = build_known_series([s["name"] for u in rows for s in (u.get("series") or []) if s.get("name")])

trust = suspect = 0
suspect_ex: list = []
for u in rows:
    if u.get("series"):
        continue
    title = (u.get("title") or "").strip()
    authors = u.get("authors") or []
    folder = Path(u["source_folder"]).name
    stems = [Path(sf["path"]).stem for sf in u.get("source_files", [])[:3]]
    albums = [(sf.get("tags") or {}).get("album") for sf in u.get("source_files", [])[:3]
              if (sf.get("tags") or {}).get("album")]
    exclude = [title, *authors]
    full = match_known_series([folder, *stems, *albums], known, exclude=exclude)
    if not full:
        continue
    # middle-only candidate set: for a `.-.` folder with >=3 segments, drop the LAST (title) segment.
    segs = folder.split(".-.")
    folder_mid = ".-.".join(segs[:-1]) if len(segs) >= 3 else folder
    mid = match_known_series([folder_mid], known, exclude=exclude)
    if mid:
        trust += 1
    else:
        suspect += 1
        if len(suspect_ex) < 30:
            suspect_ex.append((folder, title, authors, full))

print(f"DB {db}")
print(f"matches from a MIDDLE/series segment (trust): {trust}")
print(f"matches ONLY from last-segment/stem (suspect): {suspect}")
print("\n== suspect (title/last-segment misread as series?) ==")
for folder, title, authors, name in suspect_ex:
    print(f"   {folder[:50]!r:52} t={title[:26]!r:28} -> {name!r}")
