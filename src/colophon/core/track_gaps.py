"""Detect missing tracks in a multi-file book's numbered sequence.

A hole in an otherwise-present single-component sequence (1,2,4 -> missing 3) or a bounded leading gap
(3,4,5 -> missing 1,2). Interior + small leading edge only; trailing truncation and disc-track 2D gaps
are out of scope (see design). This is a help-find-problems signal: WARN and dismissible, not a proof.
Pure: no I/O. Takes plain parallel lists (one entry per file) so it never imports `classify`."""

from __future__ import annotations

from colophon.core.models import Finding, FindingCode, FindingSeverity
from colophon.core.sequence_marker import find_parts
from colophon.core.track_index import parse_track_indices

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
    """Missing parts when every file carries a canonical `N of M` PART marker sharing one total M: the
    parts are exactly `{1..M}`, so any not covered — INCLUDING a trailing truncation (files 1..8 of 11)
    the index-only guess cannot see — is a hole. A span file ('01-06 of 20') covers its whole run, so a
    range-partitioned book counts correctly. Returns [] when complete, None when it does not apply (no
    `N of M`, disagreeing totals, out-of-range, or OVERLAPPING coverage — e.g. a multi-track-per-disc
    book whose files all read 'Disc 01 of 11', where the parts do not partition 1..M)."""
    covered: set[int] = set()
    span_total = 0
    totals: set[int] = set()
    for s in stems:
        marks = [m for _, m in find_parts(s) if m.total is not None]
        if not marks:
            return None
        c = marks[0].covers()
        span_total += len(c)
        covered |= c
        totals.add(marks[0].total)   # type: ignore[arg-type]
    if len(totals) != 1:
        return None
    total = next(iter(totals))
    if span_total != len(covered) or any(i > total for i in covered):
        return None       # overlapping/duplicate coverage or an index past M -> not a clean partition
    missing = sorted(set(range(1, total + 1)) - covered)
    if len(missing) > len(covered):   # density gate: more missing than present is too uncertain to
        return []                     # assert (a lone '1 of 20' file) -> [] = no gap asserted
    return missing


def missing_tracks_finding(tracks: list[int | None], stems: list[str]) -> Finding | None:
    """A MISSING_TRACKS finding when the files (parallel `tracks`/`stems`) form a numbered sequence with
    holes, else None. The caller restricts this to multi-file SINGLE books. A canonical `N of M` total
    settles the count when present (and catches trailing truncation); otherwise the index-only sequence
    is used."""
    holes = total_based_gaps(stems)      # its own density gate; covers spans + trailing truncation
    if holes is None:
        idx = index_sequence(tracks, stems)
        if idx is None:
            return None
        holes = sequence_gaps(idx)
    if not holes:
        return None
    shown = ", ".join(str(h) for h in holes[:10]) + (" …" if len(holes) > 10 else "")
    return Finding(code=FindingCode.MISSING_TRACKS, severity=FindingSeverity.WARN,
                   detail=f"missing track(s): {shown}")
