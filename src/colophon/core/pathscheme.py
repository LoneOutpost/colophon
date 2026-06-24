"""Expand LazyLibrarian's $-token path grammar and build sanitized target paths."""

from __future__ import annotations

import re
from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.core.models import BookUnit, SeriesRef
from colophon.core.tokens import BUILD_TOKENS

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Match any $word token; \w+ captures the whole identifier so a dict lookup is
# exact (no $PadNum-vs-$Pad ambiguity) and unknown tokens map to "".
_TOKEN = re.compile(r"\$(\w+)")
_DOLLAR_SENTINEL = "\x00DOLLAR\x00"  # protects "$$" through token substitution


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
    narrator = book.narrators[0] if book.narrators else ""
    abridged = ""
    if book.abridged is not None:
        abridged = "Abridged" if book.abridged else "Unabridged"
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
        "Narrator": narrator,
        "Part": "",
        "Total": "",
        "Abridged": abridged,
    }


def expand_pattern(pattern: str, book: BookUnit) -> str:
    values = _token_values(book)
    assert values.keys() == {t.name for t in BUILD_TOKENS}, "pathscheme/tokens drift"
    protected = pattern.replace("$$", _DOLLAR_SENTINEL)
    expanded = _TOKEN.sub(lambda m: values.get(m.group(1), ""), protected)
    return expanded.replace(_DOLLAR_SENTINEL, "$")


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


def _sample_book() -> BookUnit:
    """A representative book for the Settings organize-pattern live preview."""
    b = BookUnit.new(source_folder=Path("/sample"))
    b.title = "The Way of Kings"
    b.authors = ["Brandon Sanderson"]
    b.narrators = ["Michael Kramer"]
    b.series = [SeriesRef(name="The Stormlight Archive", sequence=1.0)]
    b.publish_year = 2010
    b.abridged = False
    return b


def sample_target(folder_pattern: str, file_pattern: str) -> str:
    """Render the relative organize path for the sample book, for a Settings preview.

    Empty patterns fall back to the same defaults `build_target_path` uses."""
    patterns = AudiobookPatterns(
        folder=folder_pattern or "$Author/$Title",
        single_file=file_pattern or "$Title",
    )
    return str(build_target_path(Path("."), patterns, _sample_book()))
