"""Resolve the play order of a multi-part book's files, or refuse when ambiguous.

Order is never guessed. Embedded track numbers win when they form a complete 1..N;
otherwise a numeric-aware filename sort is used; if even that is ambiguous (two files
share a sort key) the book is blocked so a wrong part number is never written.
"""

from __future__ import annotations

import re

from colophon.core.models import SourceFile

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
            return [f for _, f in sorted(zip(nums, files), key=lambda pair: pair[0])]
    keys = [_natural_key(f.path.name) for f in files]
    if len(set(keys)) != len(files):
        return None
    return [f for _, f in sorted(zip(keys, files), key=lambda pair: pair[0])]
