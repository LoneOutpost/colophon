"""Parse a filename-derived index string into a comparable composite track index.

Foundation for MISSING_TRACKS gap detection. `parse_track_index` reads a *leading* index and drops
any trailing title text (glued like "83Cujo" or separated like "051 - Stephen King"); a lone trailing
letter is a sub-part ("06b"), adjacent numbers are a disc-track compound ("02-01"). Pure: no I/O.
Examples in tests are illustrative, not exhaustive — anything the rules can't resolve is None (the safe
direction: no gap signal)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Mirror sequence_affix._NUM's 1-3 digit rule so a 4-digit year is never an index. Kept local to avoid
# a private cross-module import; identical rule. The (?!\d) guard stops "1984" matching as "198".
_INT = r"\d{1,3}(?!\d)"
_MARK = r"(?:cd|disc|d|t)"                    # compact disc/track markers glued to digits: cd01, d2, t01
_COMPONENT = re.compile(rf"{_MARK}?({_INT})", re.IGNORECASE)   # optional compact marker + integer
_DECIMAL = re.compile(r"\.(\d{1,2})")
_LETTER_SUBPART = re.compile(r"([A-Za-z])(?![A-Za-z0-9])")     # a single letter at a boundary
_MARKER_LED = re.compile(rf"{_MARK}\d", re.IGNORECASE)         # a glued next component (d2 -> t01)


@dataclass(frozen=True, order=True)
class TrackIndex:
    components: tuple[int, ...]   # major -> minor: (2, 1) = disc 2 / track 1
    subpart: str = ""            # single letter or decimal tail; sorts within equal components, never a gap


def parse_track_index(value: str) -> TrackIndex | None:
    """A leading track index from `value`, tolerant of trailing title text, or None. See module docstring."""
    s = value.strip()
    components: list[int] = []
    subpart = ""
    i = 0
    while i < len(s):
        m = _COMPONENT.match(s, i)
        if m is None:
            break
        components.append(int(m.group(1)))
        i = m.end()
        dm = _DECIMAL.match(s, i)                 # "12.5" -> subpart "5", ends the index
        if dm:
            subpart = dm.group(1)
            i = dm.end()
            break
        lm = _LETTER_SUBPART.match(s, i)          # "06b" -> subpart "b", ends the index
        if lm:
            subpart = lm.group(1).lower()
            break
        if i < len(s) and s[i] in "-_":           # separator: continue only if another component follows
            if re.match(rf"{_MARK}?{_INT}", s[i + 1:], re.IGNORECASE):
                i += 1
                continue
            break                                 # separator then title text -> stop
        if _MARKER_LED.match(s, i):               # glued next component ("d2t01": after d2 comes t01)
            continue
        break                                     # glued/other trailing text -> title, stop
    if not components:
        return None
    return TrackIndex(tuple(components), subpart)
