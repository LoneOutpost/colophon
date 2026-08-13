"""Extract the title token(s) from an identity source (folder name / filename) by tokenizing it and
removing the tokens that play OTHER roles — a known author or series (matched with normalize_key, which
folds initials/punctuation/PascalCase so 'HP Lovecraft' == 'H. P. Lovecraft'), a `Bk N`/`Book N`/`Vol
N` marker, and structural/index/bracketed tokens. General and structural: roles are identified by
cross-reference and markers, never by a corpus-specific encoding suffix. See
tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

import re

from colophon.core.cohort_constancy import _tokens
from colophon.core.metadata_quality import is_structural_marker
from colophon.core.normalize import normalize_key

_SERIES_MARKER = re.compile(r"\b(?:bk|book|vol|volume)\s*\d", re.IGNORECASE)

# A token that is NOTHING but an index or disc marker ('1/9', '01-12', '001 of 153', 'CD01') — never a
# title, drop it whole.
_TOK_PURE_INDEX = re.compile(
    r"^\s*(?:cd|disc|disk|dvd)?\s*\.?\s*\d{1,3}(?:\s*(?:of|/|[-–])\s*\d{1,3})?\s*$", re.IGNORECASE)  # noqa: RUF001
# A leading disc/CD marker or a padded / 'N of M' index prefix glued to the title ('CD01 Some Title',
# '01 The Coming') — strip it, keep the title after.
_TOK_LEAD_MARK = re.compile(
    r"^(?:(?:cd|disc|disk|dvd)\s*\.?\s*\d+|0\d{1,2}(?!\d)|\d{1,3}(?!\d)\s*(?:of|/)\s*\d{1,3})[\s.\-_]+(?=\S)",
    re.IGNORECASE)
# A trailing padded index / N-N range / disc marker — strip it (NOT a bare unpadded number, which is
# part of the title: 'Slaughterhouse 5').
_TOK_TRAIL_MARK = re.compile(
    r"[\s.\-_]+(?:(?:cd|disc|disk|dvd)\s*\.?\s*\d+(?:\s*(?:of|[-./])\s*\d+)?"
    r"|0\d{1,2}(?!\d)|\d{1,3}[-/]\d{1,3})\s*$", re.IGNORECASE)


def _clean_token(tok: str) -> str | None:
    """Strip a leading/trailing index or disc marker off a title token; return None if the token is
    nothing but such a marker. A bare unpadded number or a 4-digit year survives (part of the title)."""
    if _TOK_PURE_INDEX.match(tok):
        return None
    t = _TOK_TRAIL_MARK.sub("", _TOK_LEAD_MARK.sub("", tok))
    t = " ".join(t.split()).strip(" .-_")
    return t or None


def title_candidates(name: str, *, authors: list[str], series: list[str]) -> list[str]:
    """The title token(s) in `name`: its ` - ` / `.-.` segments, each cleaned of index/disc affixes,
    minus tokens playing other roles.

    Author/series removal MAY return [] (an author-only folder has no title here); structural removal
    is skipped when it would remove the last token, so a bare-number title like "1984" survives."""
    akeys = {normalize_key(a) for a in authors}
    skeys = {normalize_key(s) for s in series}
    kept: list[str] = []
    for raw in _tokens(name):
        if raw.startswith(("(", "[")):
            continue
        t = _clean_token(raw)
        if t is None or normalize_key(t) in akeys or normalize_key(t) in skeys or _SERIES_MARKER.search(t):
            continue
        kept.append(t)
    if not kept:
        return []
    non_structural = [t for t in kept if not is_structural_marker(t)]
    return non_structural if non_structural else kept
