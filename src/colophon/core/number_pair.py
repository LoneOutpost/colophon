"""Extract enumeration number-PAIRS ("N of M", "1/8") from a string — the shared "is there a
part-of-total count here?" utility.

Distinct from `track_index` (which reads a leading *position* N): this finds the *total* M that marks
a value as a per-file enumeration fragment ("1 of 8 Diana Gabaldon" — the "1 of 8" is the fragment).
Reused by the field-value junk scorers and the grouping engine's number-pair axiom, so "what is an
enumeration pair" has ONE definition. Pure: no I/O.

Only an EXPLICIT connector ("of" or "/") is treated as a pair here — it is unambiguous. A bare
hyphen/underscore/space between two numbers is ambiguous in a lone string (a range, a disc-track
compound, two unrelated indices) and is left to the cohort-level grouping axiom, which has the
sibling-file context to disambiguate. A 4-digit number never participates (a year, not a count) —
mirrors `track_index._INT`'s `\\d{1,3}(?!\\d)` rule so "2001 of ..." and "1984" are never components."""

from __future__ import annotations

import re
from dataclasses import dataclass

_INT = r"\d{1,3}(?!\d)"                                       # 1-3 digits, never a 4-digit year
_ENUM = re.compile(rf"\b({_INT})\s*(?:of|/)\s*({_INT})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Enumeration:
    n: int                       # the position
    m: int                       # the total
    span: tuple[int, int]        # (start, end) in the source string


def extract_enumeration(value: str | None) -> tuple[str, list[Enumeration]]:
    """Return `(residue, pairs)`: every explicit "N of M" / "N/M" pair in `value`, and `value` with
    those spans removed (whitespace-collapsed) so the clean candidate residue remains. No pair -> the
    value is returned unchanged with an empty list. 4-digit numbers never match."""
    if not value:
        return value or "", []
    pairs = [Enumeration(int(m.group(1)), int(m.group(2)), (m.start(), m.end()))
             for m in _ENUM.finditer(value)]
    if not pairs:
        return value, []
    residue = " ".join(_ENUM.sub(" ", value).split())
    return residue, pairs
