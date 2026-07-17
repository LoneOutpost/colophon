"""Expand LazyLibrarian's $-token path grammar and build sanitized target paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from colophon.adapters.lazylibrarian import PathPatterns
from colophon.core.models import BookUnit, SeriesRef, SourceFile
from colophon.core.tokens import BUILD_TOKENS

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Match any $word token; \w+ captures the whole identifier so a dict lookup is
# exact (no $PadNum-vs-$Pad ambiguity) and unknown tokens map to "".
_TOKEN = re.compile(r"\$(\w+)")
_DOLLAR_SENTINEL = "\x00DOLLAR\x00"  # protects "$$" through token substitution
_LBRACK_SENTINEL = "\x00LBRACK\x00"  # protects "[[" (a literal "[") from group parsing
_RBRACK_SENTINEL = "\x00RBRACK\x00"  # protects "]]" (a literal "]") from group parsing


@dataclass(frozen=True)
class SeriesFormat:
    """The three LazyLibrarian series sub-patterns that compose the $Series label.

    `$FmtName` expands `name`, `$FmtNum` expands `number`, and `$Series` expands
    `series` (which may reference both). Each derived token renders empty when the
    variables it references are blank (a book with no series, or a series with no
    number), so `series` brackets the separator ([ $FmtNum]) to drop it as a unit."""

    series: str = "($FmtName[ $FmtNum])"
    name: str = "$SerName"
    number: str = "Book #$SerNum"


DEFAULT_SERIES = SeriesFormat()


def _sort_author(author: str) -> str:
    parts = author.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else author


def _sort_title(title: str) -> str:
    return re.sub(r"^(the|a|an)\s+", "", title, flags=re.IGNORECASE)


def _token_values(
    book: BookUnit,
    series_fmt: SeriesFormat = DEFAULT_SERIES,
    *,
    part: int | None = None,
    total: int | None = None,
) -> dict[str, str]:
    author = book.authors[0] if book.authors else ""
    series = book.series[0] if book.series else None
    ser_name = series.name if series else ""
    language = book.language or ""
    pub_year = str(book.publish_year) if book.publish_year is not None else ""
    sernum = ""
    padnum = ""
    if series and series.sequence is not None:
        seq = series.sequence
        sernum = str(int(seq)) if seq == int(seq) else str(seq)
        padnum = sernum.zfill(2) if seq == int(seq) else sernum
    # Compose the series label from the sub-patterns. Everything series-derived is empty
    # when the book has no series, so $Series / $FmtName / $FmtNum drop as a unit.
    fmt_name = fmt_num = series_label = ""
    if series:
        base = {
            "SerName": ser_name, "SerNum": sernum, "PadNum": padnum,
            "PubYear": pub_year, "Language": language,
        }
        fmt_name = _render_derived(series_fmt.name, base)
        fmt_num = _render_derived(series_fmt.number, base)
        series_label = _render(series_fmt.series, {**base, "FmtName": fmt_name, "FmtNum": fmt_num})
    narrator = book.narrators[0] if book.narrators else ""
    abridged = ""
    if book.abridged is not None:
        abridged = "Abridged" if book.abridged else "Unabridged"
    part_s = ""
    total_s = ""
    if part is not None and total is not None:
        width = max(2, len(str(total)))
        part_s = str(part).zfill(width)
        total_s = str(total).zfill(width)
    return {
        "Author": author,
        "SortAuthor": _sort_author(author),
        "Title": book.title or "",
        "SortTitle": _sort_title(book.title or ""),
        "Franchise": book.franchise or "",
        "Series": series_label,
        "SerName": ser_name,
        "SerNum": sernum,
        "FmtName": fmt_name,
        "FmtNum": fmt_num,
        "Language": language,
        "PadNum": padnum,
        "PubYear": pub_year,
        "Narrator": narrator,
        "Part": part_s,
        "Total": total_s,
        "Abridged": abridged,
    }


def _substitute(text: str, values: dict[str, str]) -> str:
    """Replace every $Token in a run of text with its value (unknown tokens -> "")."""
    return _TOKEN.sub(lambda m: values.get(m.group(1), ""), text)


def _group_is_empty(content: str, values: dict[str, str]) -> bool:
    """A conditional group drops if ANY $Token inside it expands to empty. A group with
    no tokens (only literals) never drops."""
    return any(values.get(m.group(1), "") == "" for m in _TOKEN.finditer(content))


def _render_derived(text: str, values: dict[str, str]) -> str:
    """Render a series sub-pattern ($FmtName / $FmtNum). Unlike a full path segment, a
    derived token collapses to empty when every $Token it references is empty, so its
    literal scaffolding (e.g. the "Book #" in "Book #$SerNum") can't leak into an outer
    optional group and keep it from dropping. A pure-literal or partly-filled pattern
    renders normally."""
    scan = text.replace("$$", _DOLLAR_SENTINEL)  # don't count an escaped "$$" as a token
    tokens = [m.group(1) for m in _TOKEN.finditer(scan)]
    if tokens and all(values.get(t, "") == "" for t in tokens):
        return ""
    return _render(text, values)


def _render(text: str, values: dict[str, str]) -> str:
    """Expand $Token markup in `text` against `values`, honoring [ ... ] conditional groups.

    A group wrapped in [ ... ] is emitted only when all of its tokens have values;
    if any is empty the whole group (literals included) is dropped. "[[" / "]]" are
    literal brackets (mirroring "$$"). Groups may not nest and must be balanced within
    a segment; an unbalanced or nested bracket raises ValueError."""
    protected = (
        text.replace("$$", _DOLLAR_SENTINEL)
        .replace("[[", _LBRACK_SENTINEL)
        .replace("]]", _RBRACK_SENTINEL)
    )
    out: list[str] = []
    i, n = 0, len(protected)
    while i < n:
        ch = protected[i]
        if ch == "[":
            end = protected.find("]", i + 1)
            if end == -1:
                raise ValueError("Unbalanced '[' bracket in pattern")
            content = protected[i + 1 : end]
            if "[" in content:
                raise ValueError("Nested '[' bracket in pattern; use adjacent groups instead")
            if not _group_is_empty(content, values):
                out.append(_substitute(content, values))
            i = end + 1
        elif ch == "]":
            raise ValueError("Unbalanced ']' bracket in pattern")
        else:
            nxt = min((p for p in (protected.find("[", i), protected.find("]", i)) if p != -1), default=n)
            out.append(_substitute(protected[i:nxt], values))
            i = nxt
    return (
        "".join(out)
        .replace(_DOLLAR_SENTINEL, "$")
        .replace(_LBRACK_SENTINEL, "[")
        .replace(_RBRACK_SENTINEL, "]")
    )


def expand_pattern(
    pattern: str,
    book: BookUnit,
    *,
    part: int | None = None,
    total: int | None = None,
    series_fmt: SeriesFormat = DEFAULT_SERIES,
) -> str:
    """Expand $Token markup for `book`, honoring [ ... ] conditional groups. The series
    sub-patterns in `series_fmt` compose the $Series / $FmtName / $FmtNum values."""
    values = _token_values(book, series_fmt, part=part, total=total)
    assert values.keys() == {t.name for t in BUILD_TOKENS}, "pathscheme/tokens drift"
    return _render(pattern, values)


def _has_token(pattern: str, name: str) -> bool:
    """True if `pattern` contains the exact $Token (not a longer look-alike)."""
    return any(m.group(1) == name for m in _TOKEN.finditer(pattern))


def ensure_part_placeholder(pattern: str) -> str:
    """Guarantee a multi-part filename pattern distinguishes parts. If the pattern
    has no $Part token, append a default ' ($Part of $Total)' suffix so N parts
    cannot collide on one filename. No-op when $Part is already present."""
    return pattern if _has_token(pattern, "Part") else f"{pattern} ($Part of $Total)"


def sanitize_segment(segment: str) -> str:
    cleaned = _ILLEGAL.sub("", segment).strip()
    return cleaned.rstrip(". ")


def _series_fmt(patterns: PathPatterns) -> SeriesFormat:
    return SeriesFormat(
        series=patterns.series_pattern,
        name=patterns.series_name_pattern,
        number=patterns.series_number_pattern,
    )


def build_target_path(root: Path, patterns: PathPatterns, book: BookUnit) -> Path:
    """Absolute target path = root / <sanitized folder segments> / <sanitized name>.m4b."""
    ser = _series_fmt(patterns)
    # Split the pattern on "/" first, then expand+sanitize each segment, so a
    # "/" inside an expanded token value cannot create an extra directory level.
    segments = [sanitize_segment(expand_pattern(s, book, series_fmt=ser)) for s in patterns.folder.split("/")]
    name_pattern = patterns.single_file or "$Title"
    filename = sanitize_segment(expand_pattern(name_pattern, book, series_fmt=ser)) + ".m4b"
    target = root
    # Empty segments (e.g. an authorless $Author) intentionally collapse: Path swallows "".
    for seg in segments:
        target = target / seg
    return target / filename


def _normalized_ext(source_ext: str) -> str:
    """A leading-dot extension, tolerant of stored values with or without the dot."""
    ext = source_ext.strip()
    if not ext:
        return ""
    return ext if ext.startswith(".") else f".{ext}"


def _book_folder(root: Path, patterns: PathPatterns, book: BookUnit) -> Path:
    ser = _series_fmt(patterns)
    folder = root
    for seg in (sanitize_segment(expand_pattern(s, book, series_fmt=ser)) for s in patterns.folder.split("/")):
        folder = folder / seg
    return folder


def build_reorg_targets(
    root: Path, patterns: PathPatterns, book: BookUnit, ordered_files: list[SourceFile]
) -> list[Path]:
    """One target path per source file for a no-encode reorg, in the given part order.

    Single-file books use the filename pattern as-is with empty $Part/$Total. Multi-part
    books (>1 file) get $Part/$Total populated and, if the pattern omits $Part, a default
    suffix appended so parts cannot collide. Each target keeps its source file's extension.
    """
    ser = _series_fmt(patterns)
    folder = _book_folder(root, patterns, book)
    total = len(ordered_files)
    base_pattern = patterns.single_file or "$Title"
    if total == 1:
        name = sanitize_segment(expand_pattern(base_pattern, book, series_fmt=ser))
        return [folder / f"{name}{_normalized_ext(ordered_files[0].ext)}"]
    name_pattern = ensure_part_placeholder(base_pattern)
    return [
        folder / f"{sanitize_segment(expand_pattern(name_pattern, book, part=i, total=total, series_fmt=ser))}{_normalized_ext(sf.ext)}"
        for i, sf in enumerate(ordered_files, start=1)
    ]


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


def sample_target(
    folder_pattern: str,
    file_pattern: str,
    *,
    series: str | None = None,
    series_name: str | None = None,
    series_number: str | None = None,
) -> str:
    """Render the relative organize path for the sample book, for a Settings preview.

    Empty patterns fall back to the same defaults `build_target_path` uses. The series
    sub-patterns, when omitted, use their LazyLibrarian defaults."""
    defaults = SeriesFormat()
    patterns = PathPatterns(
        folder=folder_pattern or "$Author/$Title",
        single_file=file_pattern or "$Title",
        series_pattern=series or defaults.series,
        series_name_pattern=series_name or defaults.name,
        series_number_pattern=series_number or defaults.number,
    )
    return str(build_target_path(Path("."), patterns, _sample_book()))
