"""Infer fields from a book folder's position in a directory tree.

Driven by a $Token scheme like "$Author/$Series/$Title": each '/'-separated level is a
$Token sub-pattern compiled by the filename parser, matched against the corresponding path
component (raw, with no extension stripping). Inference only fires when the folder's depth
under the scan root exactly matches the number of levels, to avoid mis-assigning fields on
irregular layouts. Weak evidence: the caller slots the result below embedded tags, the
datafile sidecar, and the filename in reconcile's precedence.
"""

from __future__ import annotations

from pathlib import Path
from re import Pattern

from colophon.core.filename_parser import compile_template


def parse_scheme(spec: str) -> list[Pattern[str]]:
    """Compile each '/'-separated level of `spec` into an anchored $Token regex. Empty
    levels are dropped. Raises ValueError on an unknown/non-parseable token (like a bad
    filename template)."""
    return [compile_template(level.strip()) for level in spec.split("/") if level.strip()]


def infer_from_path(folder: Path, root: Path, patterns: list[Pattern[str]]) -> dict[str, str]:
    """Match `folder`'s path components (relative to `root`) against `patterns`, only when
    the depth matches. Returns the merged captured fields (author/series/sequence/title/...);
    empty dict if `patterns` is empty, the depth mismatches, or the folder is not under root.
    A level whose component does not match contributes nothing (lenient)."""
    if not patterns:
        return {}
    try:
        parts = folder.relative_to(root).parts
    except ValueError:
        return {}
    if len(parts) != len(patterns):
        return {}
    merged: dict[str, str] = {}
    for pattern, part in zip(patterns, parts, strict=True):
        match = pattern.match(part)
        if match is not None:
            merged.update({k: v.strip() for k, v in match.groupdict().items()})
    return merged
