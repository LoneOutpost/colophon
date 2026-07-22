"""Unpack a folder name into a book's title, year, and narrators.

Audiobook folders are often `YEAR - Title (… read by Narrator)`. Parsing the folder name into fields
at scan/identify lets the title be clean and the year/narrator populate their own fields; organize's
pattern decides whether the narrator reappears in the output path. Pure: no I/O. Illustrative, not
exhaustive — an un-matching name returns as-is with year=None, narrators=[]."""

from __future__ import annotations

import re
from typing import NamedTuple

_YEAR_PREFIX = re.compile(r"^\s*(\d{4})\s*[-–—_]\s*")            # "1981 - ", "1990 – ", "2001_"  # noqa: RUF001, RUF003
_READ_BY = re.compile(r"read by\s+([^)]+)", re.IGNORECASE)       # "read by Lorna Raver" (up to ')')
_NARR_SPLIT = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)
_TRAILING_SEP = re.compile(r"[\s\-–—]+$")  # noqa: RUF001


class FolderTitle(NamedTuple):
    title: str
    year: int | None
    narrators: list[str]


def parse_folder_title(name: str) -> FolderTitle:
    """`YEAR - Title (… read by A and B)` -> (title, year, [A, B]). Strips a leading 4-digit year and a
    `read by …` span (re-closing an edition parenthetical), leaving the rest as the title."""
    s = name.strip()

    year: int | None = None
    m = _YEAR_PREFIX.match(s)
    if m:
        y = int(m.group(1))
        if 1000 <= y <= 2999:
            year = y
            s = s[m.end():]

    narrators: list[str] = []
    rm = _READ_BY.search(s)
    if rm:
        names = rm.group(1).strip().rstrip(")").strip()
        narrators = [n.strip() for n in _NARR_SPLIT.split(names) if n.strip()]
        open_paren = s.rfind("(", 0, rm.start())
        close_paren = s.find(")", rm.start())
        tail = s[close_paren + 1:] if close_paren != -1 else ""
        if open_paren != -1:
            inner = _TRAILING_SEP.sub("", s[open_paren + 1:rm.start()]).strip()
            head = s[:open_paren].rstrip()
            s = f"{head} ({inner}){tail}" if inner else f"{head}{tail}"
        else:  # bare "Title - read by X" with no parenthetical
            s = _TRAILING_SEP.sub("", s[:rm.start()])

    return FolderTitle(re.sub(r"\s+", " ", s).strip(), year, narrators)
