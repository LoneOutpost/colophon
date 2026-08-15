"""Resolve the play order of a multi-part book's files, or refuse when ambiguous.

Order is never guessed. Embedded track numbers win when they form a complete 1..N; then a canonical
`N of M` part index does the same (every file an `N of M`, consistent M, indices exactly 1..M);
otherwise a numeric-aware filename sort is used; if even that is ambiguous (two files share a sort key)
the book is blocked so a wrong part number is never written.
"""

from __future__ import annotations

import re

from colophon.core.models import SourceFile
from colophon.core.sequence_marker import find_parts

_NUM = re.compile(r"\d+")


def _natural_key(name: str) -> tuple:
    """A numeric-aware sort key: digit runs compare as integers ('Part 2' < 'Part 10')."""
    parts = _NUM.split(name)
    nums = [int(n) for n in _NUM.findall(name)]
    key: list = []
    for i, chunk in enumerate(parts):
        key.append(chunk.lower())
        if i < len(nums):
            key.append(nums[i])
    return tuple(key)


def _complete_part_order(files: list[SourceFile]) -> list[SourceFile] | None:
    """Order by the canonical `N of M` PART index, but only at high confidence: every file must carry an
    `N of M` marker, the totals must agree on one M, and the indices must be exactly 1..M. Returns None
    otherwise so the caller falls through to the filename sort. Mirrors the embedded-track 1..N rule; it
    corrects a book whose filenames are textually inconsistent (`Foster-` vs `Foster -`) but whose
    `N of M` is clean, and never fires for a disc/track book with disagreeing totals."""
    indexed: list[tuple[tuple[int, str], SourceFile]] = []
    totals: set[int] = set()
    for f in files:
        marks = [m for _, m in find_parts(f.path.stem) if m.total is not None]
        if not marks:
            return None
        indexed.append(((marks[0].index, marks[0].subpart), f))
        totals.add(marks[0].total)   # type: ignore[arg-type]
    if len(totals) != 1:
        return None
    total = next(iter(totals))
    if len(files) != total or sorted(i for (i, _s), _f in indexed) != list(range(1, total + 1)):
        return None
    return [f for _, f in sorted(indexed, key=lambda pair: pair[0])]


def resolve_part_order(
    files: list[SourceFile], tracks: list[int | None]
) -> list[SourceFile] | None:
    """Return `files` in part order, or None when order cannot be determined.

    `tracks[i]` is the embedded track number of `files[i]` (or None). Returns None
    only when the filename sort is ambiguous (duplicate sort keys).
    """
    if len(files) <= 1:
        return list(files)
    if all(t is not None for t in tracks):
        nums = [int(t) for t in tracks]  # type: ignore[arg-type]
        if sorted(nums) == list(range(1, len(files) + 1)):
            return [f for _, f in sorted(zip(nums, files, strict=True), key=lambda pair: pair[0])]
    by_part = _complete_part_order(files)     # canonical 'N of M', complete 1..M
    if by_part is not None:
        return by_part
    keys = [_natural_key(f.path.name) for f in files]
    if len(set(keys)) != len(files):
        return None
    return [f for _, f in sorted(zip(keys, files, strict=True), key=lambda pair: pair[0])]
