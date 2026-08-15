"""Detect missing tracks in a multi-file book's numbered sequence.

A hole in an otherwise-present single-component sequence (1,2,4 -> missing 3) or a bounded leading gap
(3,4,5 -> missing 1,2). Interior + small leading edge only; trailing truncation and disc-track 2D gaps
are out of scope (see design). This is a help-find-problems signal: WARN and dismissible, not a proof.
Pure: no I/O. Takes plain parallel lists (one entry per file) so it never imports `classify`."""

from __future__ import annotations

import re

from colophon.core.models import Finding, FindingCode, FindingSeverity
from colophon.core.sequence_marker import find_parts
from colophon.core.track_index import parse_track_indices

# 'NN-NN of M' is a track RANGE (one file spans parts NN..NN of M), not a single 'N of M' index — the
# deferred range family. A book whose files carry ranges can't be part-counted this way, so we defer it.
_RANGE_OF = re.compile(r"\d{1,3}\s*-\s*\d{1,3}\s+of\s+\d{1,3}", re.IGNORECASE)

_MIN_FILES = 3        # too few files to assert a sequence
_LEADING_MAX = 3      # infer a missing leading track only when the sequence starts at 2 or 3


def index_sequence(tracks: list[int | None], stems: list[str]) -> list[int] | None:
    """One integer index per file (parallel `tracks`/`stems`), tag-preferred, or None when there is no
    clean single-component sequence. Both paths are all-or-nothing: every file must contribute a
    distinct index, so a later hole means a genuinely absent track, not an unparsed name."""
    if tracks and all(t is not None for t in tracks):
        ints = [t for t in tracks if t is not None]
        if len(set(ints)) == len(ints):
            return ints
    parsed = parse_track_indices(stems)
    if parsed and all(p is not None and len(p.components) == 1 for p in parsed):
        ints = [p.components[0] for p in parsed if p is not None]
        if len(set(ints)) == len(ints):
            return ints
    return None


def sequence_gaps(indices: list[int]) -> list[int]:
    """Interior holes plus a bounded leading edge of a numbered sequence, behind a density gate; [] when
    there is no confident gap. Interior-only above `lo`; leading edge only when 1 < lo <= 3 (so a
    continuation volume starting at 51 is not read as 50 missing)."""
    present = sorted(set(indices))
    if len(present) < _MIN_FILES:
        return []
    lo, hi = present[0], present[-1]
    present_set = set(present)
    interior = [n for n in range(lo, hi + 1) if n not in present_set]
    leading = list(range(1, lo)) if 1 < lo <= _LEADING_MAX else []
    holes = leading + interior
    if holes and len(holes) <= len(present):
        return holes
    return []


def total_based_gaps(stems: list[str]) -> list[int] | None:
    """Missing parts when every file carries a canonical `N of M` PART index sharing one total M: the
    parts are exactly `{1..M}`, so any absent one — INCLUDING a trailing truncation (files 1..8 of 11)
    the index-only guess cannot see — is a hole. Returns [] when complete, None when it does not apply
    (a range file, no `N of M`, disagreeing totals, a duplicate/out-of-range index — e.g. a
    multi-track-per-disc book whose files all read 'Disc 01 of 11')."""
    idx: list[int] = []
    totals: set[int] = set()
    for s in stems:
        if _RANGE_OF.search(s):
            return None
        marks = [m for _, m in find_parts(s) if m.total is not None]
        if not marks:
            return None
        idx.append(marks[0].index)
        totals.add(marks[0].total)   # type: ignore[arg-type]
    if len(totals) != 1:
        return None
    total = next(iter(totals))
    present = set(idx)
    if len(present) != len(idx) or any(i > total for i in present):
        return None
    return sorted(set(range(1, total + 1)) - present)


def missing_tracks_finding(tracks: list[int | None], stems: list[str]) -> Finding | None:
    """A MISSING_TRACKS finding when the files (parallel `tracks`/`stems`) form a numbered sequence with
    holes, else None. The caller restricts this to multi-file SINGLE books. A canonical `N of M` total
    settles the count when present (and catches trailing truncation); otherwise the index-only sequence
    is used."""
    holes = total_based_gaps(stems)
    if holes is None:
        idx = index_sequence(tracks, stems)
        if idx is None:
            return None
        holes = sequence_gaps(idx)
    elif len(holes) > len(stems):        # density gate (matches sequence_gaps): more missing than
        holes = []                       # present is too uncertain to assert
    if not holes:
        return None
    shown = ", ".join(str(h) for h in holes[:10]) + (" …" if len(holes) > 10 else "")
    return Finding(code=FindingCode.MISSING_TRACKS, severity=FindingSeverity.WARN,
                   detail=f"missing track(s): {shown}")
