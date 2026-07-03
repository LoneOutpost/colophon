"""Detect a sequence-number affix on a name (folder / file stem / tag value) and strip it.

A leading '02 - Yendi' or trailing 'Yendi (2)' carries a series sequence number stuck to a
title. This is the ONE place that decides whether such a number is present, what the clean
title is, and how confident we are. Reused by the numbered-siblings classifier axiom, the
lone-title cleaner in IDENTIFY, and filename_cluster — so number-stripping has one behavior
and one whitespace guard. Pure: no I/O.

Confidence:
- "strong": the number is bracket-delimited ('1) …', '… (2)', '… [3]') OR there is whitespace
  on at least one side of the separator ('02 - Yendi'). Safe to strip even for a lone book.
- "weak": a separator with no surrounding whitespace ('30-Day', 'Catch-22') — reads as a
  compound; only trust it with corroboration (a sibling ramp or a series tag).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Separators a leading number may hang off: dash, underscore, close-paren/bracket, dot, en/em dash.
_LEAD_SEPS = r"-_)\].–—"  # noqa: RUF001
# Trailing separators (bare, non-bracket): dash / underscore / en / em dash. Brackets handled apart.
_TRAIL_SEPS = r"-_–—"  # noqa: RUF001
_NUM = r"\d{1,3}(?:\.\d{1,2})?"          # 1-3 integer digits (4-digit years excluded), optional decimal

_LEADING = re.compile(rf"^\s*(?P<num>{_NUM})(?P<lsp>\s*)(?P<sep>[{_LEAD_SEPS}])(?P<rsp>\s*)(?P<rest>\S.*)$")
_TRAIL_BRACKET = re.compile(rf"^(?P<rest>.*?\S)\s*[(\[]\s*(?P<num>{_NUM})\s*[)\]]\s*$")
_TRAIL_SEP = re.compile(rf"^(?P<rest>.*?\S)(?P<lsp>\s*)[{_TRAIL_SEPS}](?P<rsp>\s*)(?P<num>{_NUM})\s*$")
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True)
class SequenceAffix:
    sequence: float
    cleaned: str
    confidence: Literal["strong", "weak"]


def _build(num: str, rest: str, *, strong: bool) -> SequenceAffix | None:
    rest = rest.strip()
    if not _HAS_LETTER.search(rest):     # the remainder must be a real title, not more digits
        return None
    return SequenceAffix(sequence=float(num), cleaned=rest, confidence="strong" if strong else "weak")


def parse_sequence_affix(name: str) -> SequenceAffix | None:
    """Return the sequence + cleaned title + confidence for a name with a numeric affix, else None."""
    if not name:
        return None
    m = _LEADING.match(name)
    if m:
        strong = m.group("sep") in ")]" or bool(m.group("lsp") or m.group("rsp"))
        return _build(m.group("num"), m.group("rest"), strong=strong)
    m = _TRAIL_BRACKET.match(name)
    if m:
        return _build(m.group("num"), m.group("rest"), strong=True)   # bracketed is always strong
    m = _TRAIL_SEP.match(name)
    if m:
        return _build(m.group("num"), m.group("rest"), strong=bool(m.group("lsp") or m.group("rsp")))
    return None
