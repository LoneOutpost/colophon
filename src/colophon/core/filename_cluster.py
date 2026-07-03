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

# Top-level chunk separators: brackets, underscore, dash. Also a dot on a letter<->digit boundary
# ("Series.01" -> "Series"|"01", "Vol.1" -> "Vol"|"1"), but NOT a dot between two digits (a decimal
# like "1.5") or between two letters (initials like "J.R.R."), which stay whole.
_SEP = re.compile(r"[()\[\]_\-]+|(?<=[A-Za-z])\.(?=\d)|(?<=\d)\.(?=[A-Za-z])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")    # camelCase boundary
_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)")  # letter->digit ONLY ("Part1"->"Part 1"; "7th" intact)
_NUM = re.compile(r"^\d+(?:\.\d+)?$")          # integer or decimal token
# A trailing sequence number within a chunk ("Wheel of Time 3"). Bounded to 1-3 integer digits + an
# optional 2-place decimal, matching sequence_affix._NUM so a 4-digit year ("Dune 1984") is never
# read as a sequence. (This chunk-local, space-separated form is why we can't just call
# parse_sequence_affix, which needs a bracket or dash separator, not a bare space.)
_TRAIL_NUM = re.compile(r"\s+\d{1,3}(?:\.\d{1,2})?\s*$")


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


def _title_chunks(chunks: list[str], stem: str = "") -> list[str]:
    """Chunks the title is built from: drop a leading number-only chunk (a track/sequence index)
    ONLY when the original stem reads as a strong sequence affix (spaced or bracketed); an unspaced
    compound like '30-Day' keeps its number. Keeps at least the last chunk."""
    from colophon.core.sequence_affix import parse_sequence_affix
    affix = parse_sequence_affix(stem)
    if affix is not None and affix.confidence == "weak":
        return chunks
    i = 0
    while i < len(chunks) - 1 and not _text_sig(_tokens(chunks[i])):
        i += 1
    return chunks[i:]


def _multi_work(file: Path, chunks: list[str]) -> DetectedWork:
    """One file = one work. Title is the leading text chunk (number-only index chunks
    dropped); series/seq from a later chunk. When the first chunk is a bare number
    (the affix was weak so we kept it), fall back to the raw stem as the display title."""
    title_chunks = _title_chunks(chunks, file.stem) if chunks else []
    first_has_text = title_chunks and _text_sig(_tokens(title_chunks[0]))
    title = _spaced(title_chunks[0]) if first_has_text else _spaced(file.stem)
    series, seq = _series_and_seq(title_chunks[1:]) if len(title_chunks) > 1 else (None, None)
    return DetectedWork(label=title or _spaced(file.stem), series=series, sequence=seq, files=[file])


def _parts_work(files: list[Path], per_file: list[list[str]]) -> DetectedWork:
    """All files are one book's parts. Title is the leading text chunk with the varying
    part number stripped."""
    first = _title_chunks(per_file[0], files[0].stem) if per_file[0] else []
    title = _strip_trailing_number(_spaced(first[0])) if first else _spaced(files[0].stem)
    return DetectedWork(label=title or _spaced(files[0].stem), series=None, sequence=None,
                        files=list(files))


def shares_token(a: str, b: str) -> bool:
    """True if the two strings share a non-numeric word (>=2 chars), case-insensitive.
    Distinguishes a title folder (name relates to the book title) from an author
    folder (name unrelated -> it is the author and the filename is the title).

    # KNOWN LIMITATION: tokenizes via _tokens, which does not split on '_' or '-'
    # (only _chunks does). A separator-joined stem like "some_book_title" stays one
    # token here, so callers comparing a raw stem may under-match. Fails safe
    # (suppresses inference) rather than corrupting. Revisit with single-file title
    # extraction in a follow-up.
    """
    ta = {t for t in _tokens(a) if not _is_num(t) and len(t) >= 2}
    tb = {t for t in _tokens(b) if not _is_num(t) and len(t) >= 2}
    return bool(ta & tb)


def cluster(files: list[Path]) -> ClusterResult:
    """Classify a folder's files into works by filename structure alone."""
    if not files:
        return ClusterResult(ContentKind.UNKNOWN, 0.0)

    per_file = [_chunks(f.stem) for f in files]
    n = len(files)

    if n == 1:
        work = _multi_work(files[0], per_file[0])
        return ClusterResult(ContentKind.SINGLE, 3.0,
                             [_signal("single_file", 3, "one file in the folder")], [work])

    if not all(per_file):  # a file produced no chunks -- can't reason
        works = [_multi_work(files[i], per_file[i]) for i in range(n)]
        return ClusterResult(ContentKind.UNKNOWN, 0.0,
                             [_signal("unparseable", 0, "a file had no parseable name")], works)

    min_len = min(len(c) for c in per_file)
    same_count = len({len(c) for c in per_file}) == 1
    rels = [_relationship([_tokens(per_file[f][i]) for f in range(n)]) for i in range(min_len)]
    has_diff = DIFFERENT_TEXT in rels
    has_match_num = MATCH_EXCEPT_NUMBER in rels

    signals: list[ConfidenceSignal] = []
    signals.append(_signal("uniform_chunk_count", 2, f"all files split into {min_len} parts")
                   if same_count else
                   _signal("ragged_chunk_count", -1, "files split into differing parts"))

    if has_diff:
        signals.append(_signal("distinct_titles", 2, "files have different title text"))
        works = [_multi_work(files[i], per_file[i]) for i in range(n)]
        kind = ContentKind.MULTI
    elif same_count or has_match_num:
        # Ragged files can hide a distinguishing title in trailing chunks we did
        # not compare; if any trailing chunk carries non-numeric text, stay UNKNOWN
        # rather than wrongly merging separate books into one.
        trailing_text = not same_count and any(
            any(_text_sig(_tokens(c)) for c in per_file[f][min_len:]) for f in range(n)
        )
        if trailing_text:
            works = [_multi_work(files[i], per_file[i]) for i in range(n)]
            kind = ContentKind.UNKNOWN
        else:
            signals.append(_signal("parts_differ_by_number", 2, "files differ only by numbers"))
            works = [_parts_work(files, per_file)]
            kind = ContentKind.SINGLE
    else:
        works = [_multi_work(files[i], per_file[i]) for i in range(n)]
        kind = ContentKind.UNKNOWN

    confidence = float(max(sum(s.points for s in signals), 0))
    return ClusterResult(kind, confidence, signals, works)
