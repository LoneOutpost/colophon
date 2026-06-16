"""Expand LazyLibrarian's $-token path grammar and build sanitized target paths."""

from __future__ import annotations

import re
from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.core.models import BookUnit

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Match any $word token; \w+ captures the whole identifier so a dict lookup is
# exact (no $PadNum-vs-$Pad ambiguity) and unknown tokens map to "".
_TOKEN = re.compile(r"\$(\w+)")


def _sort_author(author: str) -> str:
    parts = author.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else author


def _sort_title(title: str) -> str:
    return re.sub(r"^(the|a|an)\s+", "", title, flags=re.IGNORECASE)


def _token_values(book: BookUnit) -> dict[str, str]:
    author = book.authors[0] if book.authors else ""
    series = book.series[0] if book.series else None
    sernum = ""
    padnum = ""
    if series and series.sequence is not None:
        seq = series.sequence
        sernum = str(int(seq)) if seq == int(seq) else str(seq)
        padnum = sernum.zfill(2) if seq == int(seq) else sernum
    return {
        "Author": author,
        "SortAuthor": _sort_author(author),
        "Title": book.title or "",
        "SortTitle": _sort_title(book.title or ""),
        "Series": series.name if series else "",
        "SerName": series.name if series else "",
        "SerNum": sernum,
        "PadNum": padnum,
        "PubYear": str(book.publish_year) if book.publish_year is not None else "",
        "Part": "",
        "Total": "",
        "Abridged": "",
    }


def expand_pattern(pattern: str, book: BookUnit) -> str:
    values = _token_values(book)
    return _TOKEN.sub(lambda m: values.get(m.group(1), ""), pattern)


def sanitize_segment(segment: str) -> str:
    cleaned = _ILLEGAL.sub("", segment).strip()
    return cleaned.rstrip(". ")


def build_target_path(root: Path, patterns: AudiobookPatterns, book: BookUnit) -> Path:
    """Absolute target path = root / <sanitized folder segments> / <sanitized name>.m4b."""
    # Split the pattern on "/" first, then expand+sanitize each segment, so a
    # "/" inside an expanded token value cannot create an extra directory level.
    segments = [sanitize_segment(expand_pattern(s, book)) for s in patterns.folder.split("/")]
    name_pattern = patterns.single_file or "$Title"
    filename = sanitize_segment(expand_pattern(name_pattern, book)) + ".m4b"
    target = root
    # Empty segments (e.g. an authorless $Author) intentionally collapse: Path swallows "".
    for seg in segments:
        target = target / seg
    return target / filename
