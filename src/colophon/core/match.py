"""Fuzzy string matching used by the confidence engine."""

from __future__ import annotations

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


def title_author_score(
    title_a: str | None,
    authors_a: list[str],
    title_b: str | None,
    authors_b: list[str],
) -> float:
    """Weighted blend: title 70%, best author-pair 30%."""
    return 0.7 * ratio(title_a, title_b) + 0.3 * _best_author_ratio(authors_a, authors_b)
