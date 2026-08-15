"""Acceptance gate for core.title_essence: run it over a colophon DB and report how many committed
titles it would change, split into likely wins (compound/noisy committed) vs likely regressions
(a clean committed title losing a word). Usage: uv run python scripts/title_essence_dryrun.py [DB]"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from colophon.core.title_essence import _NOISE, _author_word_keys, _key, title_essence

db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/colophon.db"
con = sqlite3.connect(db)
agree = wins = regress = 0
win_ex: list = []
reg_ex: list = []
for (data,) in con.execute("SELECT data FROM book_units"):
    u = json.loads(data)
    cur = (u.get("title") or "").strip()
    if not cur:
        continue
    authors = u.get("authors") or []
    # APPLY policy (mirrors title_essence_for_book): only clean a title that shows structural noise.
    first = cur.split()[0] if cur.split() else ""
    if not (_NOISE.search(cur) or _key(first) in _author_word_keys(authors)):
        agree += 1
        continue
    out = title_essence(
        cur,
        folder_name=Path(u["source_folder"]).name,
        file_stems=[Path(sf["path"]).stem for sf in u.get("source_files", [])],
        tag_titles=[(sf.get("tags") or {}).get("title") for sf in u.get("source_files", []) if (sf.get("tags") or {}).get("title")],
        authors=authors,
        series=[s["name"] for s in (u.get("series") or [])],
    )
    if out is None or out.casefold() == cur.casefold():
        agree += 1
        continue
    compound = (" - " in cur) or (":" in cur) or bool(re.search(r"\bBk\b|\bBook \d|\bUnabridged\b|\d", cur)) or len(cur) > 55
    if compound:
        wins += 1
        if len(win_ex) < 15:
            win_ex.append((cur, out))
    else:
        regress += 1
        if len(reg_ex) < 25:
            reg_ex.append((cur, out))

print(f"DB {db}")
print(f"unchanged {agree} | likely WINS {wins} | likely REGRESSIONS {regress}")
print("\n== wins (compound committed -> cleaned) ==")
for c, o in win_ex:
    print(f"   {c!r:52} -> {o!r}")
print("\n== regressions (clean committed lost a word) ==")
for c, o in reg_ex:
    print(f"   {c!r:44} -> {o!r}")
