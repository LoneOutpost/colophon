"""Cluster a folder's audio files into works by filename structure alone.

Used when embedded tags give no grouping signal. Each filename stem is chunked
on separators, normalized, and compared positionally across the folder's files:
files that differ only by numbers are parts of one book; files whose text
differs are separate books (a shared, number-varying chunk is their series).
Pure: no I/O. "Close, not perfect" -- the source matcher refines residue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.models import ConfidenceSignal, ContentKind, DetectedWork

_SEP = re.compile(r"[()\[\]_\-]+")             # top-level chunk separators
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")    # camelCase boundary
_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)")  # letter->digit ONLY ("Part1"->"Part 1"; "7th" intact)
_NUM = re.compile(r"^\d+(?:\.\d+)?$")          # integer or decimal token
_TRAIL_NUM = re.compile(r"\s+\d+(?:\.\d+)?\s*$")


@dataclass(frozen=True)
class ClusterResult:
    content_kind: ContentKind
    confidence: float
    signals: list[ConfidenceSignal] = field(default_factory=list)
    detected_works: list[DetectedWork] = field(default_factory=list)


def _chunks(stem: str) -> list[str]:
    """Split a filename stem into ordered chunks on separators; drop empties."""
    return [c.strip() for c in _SEP.split(stem) if c.strip()]


def _spaced(chunk: str) -> str:
    """Display form: space camelCase and letter->digit boundaries, commas to
    spaces, collapse whitespace. Case preserved; ordinals like '7th' stay intact."""
    s = _CAMEL.sub(" ", chunk)
    s = _LETTER_DIGIT.sub(" ", s)
    s = s.replace(",", " ")
    return re.sub(r"\s+", " ", s).strip()


def _tokens(chunk: str) -> list[str]:
    """Lowercased word/number tokens for comparison."""
    return _spaced(chunk).lower().split()


def _is_num(tok: str) -> bool:
    return bool(_NUM.match(tok))


def _text_sig(tokens: list[str]) -> tuple[str, ...]:
    """The non-number tokens -- a chunk's 'text signature'."""
    return tuple(t for t in tokens if not _is_num(t))


def _trailing_number(text: str) -> float | None:
    m = _TRAIL_NUM.search(text)
    return float(m.group().strip()) if m else None


def _strip_trailing_number(text: str) -> str:
    return _TRAIL_NUM.sub("", text).strip()
