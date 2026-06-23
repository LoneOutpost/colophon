"""Shared helpers for the delimiter-joined list-field convention.

Several fields (authors, narrators, genres, tags) round-trip between a typed
``list[str]`` and a single delimited string for editing and tag projection.
These helpers are the single source of truth for that split/join, so the
separator and the empty-handling rules can't drift between call sites.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable


def split_list(value: str | None, sep: str = ";") -> list[str]:
    """Split a delimited string into trimmed, non-empty parts (``None`` -> [])."""
    if not value:
        return []
    return [part.strip() for part in value.split(sep) if part.strip()]


def join_list(items: Iterable[str], sep: str = "; ") -> str | None:
    """Join a list of strings for display/storage, or ``None`` when empty."""
    return sep.join(items) or None


def dedupe_preserving(
    items: Iterable[str], *, key: Callable[[str], str] | None = None
) -> list[str]:
    """Drop duplicates while preserving first-seen order. ``key`` selects the
    identity used for comparison (e.g. ``str.casefold`` for case-insensitive)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        k = key(item) if key is not None else item
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out
