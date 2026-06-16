"""Fuzzy string matching used by the confidence engine."""

from __future__ import annotations

from difflib import SequenceMatcher


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def ratio(a: str | None, b: str | None) -> float:
    """Similarity in [0, 1]; 0 if either side is empty."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


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
