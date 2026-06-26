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


IDENTICAL = "identical"
MATCH_EXCEPT_NUMBER = "match_except_number"
DIFFERENT_TEXT = "different_text"


def _relationship(col_tokens: list[list[str]]) -> str:
    """Classify one position across files (token lists, one per file, all present)."""
    if len({_text_sig(t) for t in col_tokens}) > 1:
        return DIFFERENT_TEXT
    if len({tuple(t) for t in col_tokens}) == 1:
        return IDENTICAL
    return MATCH_EXCEPT_NUMBER


def _signal(name: str, points: int, detail: str) -> ConfidenceSignal:
    return ConfidenceSignal(name=name, points=points, detail=detail)


def _series_and_seq(chunks: list[str]) -> tuple[str | None, float | None]:
    """First chunk that ends in a number -> (series_name, sequence)."""
    for chunk in chunks:
        disp = _spaced(chunk)
        seq = _trailing_number(disp)
        if seq is not None:
            return (_strip_trailing_number(disp) or None), seq
    return None, None


def _multi_work(file: Path, chunks: list[str]) -> DetectedWork:
    """One file = one work. Title is the leading chunk; series/seq from a later chunk."""
    title = _spaced(chunks[0]) if chunks else _spaced(file.stem)
    series, seq = _series_and_seq(chunks[1:]) if len(chunks) > 1 else (None, None)
    return DetectedWork(label=title or _spaced(file.stem), series=series, sequence=seq, files=[file])


def _parts_work(files: list[Path], per_file: list[list[str]]) -> DetectedWork:
    """All files are one book's parts. Title is the leading chunk with the varying
    part number stripped."""
    first = per_file[0]
    title = _strip_trailing_number(_spaced(first[0])) if first else _spaced(files[0].stem)
    return DetectedWork(label=title or _spaced(files[0].stem), series=None, sequence=None,
                        files=list(files))
