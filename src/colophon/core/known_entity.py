"""Cross-reference a book's tokens against the entity graph's KNOWN series (and later authors), so a
book whose series has no local marker still resolves when the same series is already established
elsewhere in the library — the payoff of maintaining the graph.

Normalized-EXACT only (a candidate equals a known series after folding case/punctuation/`Name NN`);
a token already identified as this book's title or author is EXCLUDED from series contention (role
mutual-exclusion), and the known set is junk-filtered so a mis-derived 'Book'/'01 of' never matches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from colophon.core.identity_tokens import parse_series_ref
from colophon.core.metadata_quality import is_structural_marker
from colophon.core.normalize import normalize_key

# A `.-.` / ' - ' / bracket splitter — brackets are separators, never indicators.
_SPLIT = re.compile(r"\.-\.| - |[\[\]()]")
_ENUM_ONLY = re.compile(r"^\d+\s*(?:of)?$", re.IGNORECASE)   # '1', '01 of' — an index fragment, not a series


def _real_series_name(name: str) -> bool:
    """A usable series name: has a letter, is not a structural marker, is not a bare index fragment,
    and is at least two characters. Filters the junk that pollutes the live series set."""
    n = (name or "").strip()
    return (len(n) >= 2 and bool(re.search(r"[^\W\d]", n))
            and not is_structural_marker(n) and not _ENUM_ONLY.match(n)
            and normalize_key(n) not in _NON_SERIES)


# Single generic words that appear as a mis-derived 'series' but never name one: bibliographic
# nouns AND the bare sequence markers ('Bk', 'Vol') a shattered folder segment can leave behind.
_NON_SERIES = {normalize_key(w) for w in (
    "book", "novel", "series", "collection", "audiobook", "part",
    "bk", "vol", "volume", "pt", "disc", "cd")}


def build_known_series(series_names: Iterable[str]) -> dict[str, str]:
    """`normalize_key(name) -> canonical name` for every REAL series in the library (junk dropped).
    First spelling wins as the canonical display."""
    known: dict[str, str] = {}
    for name in series_names:
        if _real_series_name(name):
            known.setdefault(normalize_key(name), name)
    return known


def _candidate_segments(strings: Iterable[str]) -> list[str]:
    out: list[str] = []
    for s in strings:
        if not s:
            continue
        for seg in _SPLIT.split(s):
            seg = seg.strip()
            if seg and not is_structural_marker(seg):
                out.append(seg)
    return out


def match_known_series(
    sources: Iterable[str], known: dict[str, str], *, exclude: Iterable[str] = (),
) -> str | None:
    """The canonical known series a book's `sources` (folder/filename/album strings) name, or None.
    A segment whose key is in `exclude` (this book's resolved title or author) is skipped — a token
    already playing another role is not a series. Matches the segment directly and its `Name NN` name
    ('Culture 06' -> 'Culture'), normalized-exact against `known`."""
    excl = {normalize_key(e) for e in exclude if e}
    for seg in _candidate_segments(sources):
        for key in (normalize_key(seg), normalize_key(parse_series_ref(seg)[0]) if parse_series_ref(seg) else None):
            if key and key not in excl and key in known:
                return known[key]
    return None
