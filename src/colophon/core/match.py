"""Fuzzy string matching used by the confidence engine."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def ratio(a: str | None, b: str | None) -> float:
    """Similarity in [0, 1]; 0 if either side is empty.

    Token-aware: the max of a character sequence ratio (robust to typos) and a
    token Jaccard overlap (robust to word reordering and minor word changes).
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    token = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    return max(seq, token)


def _best_author_ratio(a_authors: list[str], b_authors: list[str]) -> float:
    if not a_authors or not b_authors:
        return 0.0
    return max(ratio(a, b) for a in a_authors for b in b_authors)


# A leading publication-year prefix: 4 digits + a dash separator. A colon is deliberately
# excluded: a leading number before a colon is subtitle punctuation ("2001: A Space Odyssey"),
# not a year convention (which uses a dash or space).
_YEAR_PREFIX_RE = re.compile(r"^\s*\(?\d{4}\)?\s*[-\u2013\u2014]\s*")
_FORMAT_KEYWORDS = (
    r"edition|unabridged|abridged|dramati[sz]ed|version|"
    r"remaster(?:ed)?|deluxe|complete|movie tie-in|audiobook"
)
_FORMAT_PAREN_RE = re.compile(
    r"\s*[(\[][^)\]]*\b(?:" + _FORMAT_KEYWORDS + r")\b[^)\]]*[)\]]", re.IGNORECASE
)
_TRAILING_FORMAT_RE = re.compile(r"\s*[-\u2013\u2014:]?\s*\b(?:unabridged|abridged)\b\s*$", re.IGNORECASE)
_TITLE_WS_RE = re.compile(r"\s+")


def clean_match_title(title: str | None, *, strip_year: bool = True) -> str:
    """Strip query/score noise from a title: a leading year+separator (only when `strip_year`),
    any parenthetical/bracket carrying an edition/format keyword, and trailing standalone format
    words. Non-keyword parentheticals (e.g. a series tag) are kept. Returns the original title when
    cleaning would empty it, and "" for a falsy title.

    `strip_year` defaults True for the match-query/scoring path, where dropping a leading year
    sharpens the query. Callers that mutate a *stored* title pass `strip_year=False`: a leading
    4-digit number and a dash is ambiguous (a publication-year prefix like "1982 - The Gunslinger"
    vs. a title that genuinely opens with a number), so the persisted title never risks that
    destructive guess."""
    if not title:
        return ""
    cleaned = _YEAR_PREFIX_RE.sub("", title) if strip_year else title
    cleaned = _FORMAT_PAREN_RE.sub("", cleaned)
    cleaned = _TRAILING_FORMAT_RE.sub("", cleaned)
    cleaned = _TITLE_WS_RE.sub(" ", cleaned).strip()
    return cleaned or title


def title_author_score(
    title_a: str | None,
    authors_a: list[str],
    title_b: str | None,
    authors_b: list[str],
) -> float:
    """Weighted blend: title 70%, best author-pair 30%."""
    return 0.7 * ratio(title_a, title_b) + 0.3 * _best_author_ratio(authors_a, authors_b)
