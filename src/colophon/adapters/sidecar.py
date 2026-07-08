"""Read an AudiobookShelf/Audible-style metadata.json datafile sidecar beside a book."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel

from colophon.core.coerce import to_float, year_or_none
from colophon.core.models import BookUnit, ContentKind

logger = logging.getLogger(__name__)

_SERIES_RE = re.compile(r"^(?P<name>.*?)\s*#\s*(?P<num>[\d.]+)\s*$")


class DatafileSidecar(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = []
    narrators: list[str] = []
    series_name: str | None = None
    series_sequence: float | None = None
    publish_year: int | None = None
    publisher: str | None = None
    description: str | None = None
    asin: str | None = None
    isbn: str | None = None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_series(series: object) -> tuple[str | None, float | None]:
    if not isinstance(series, list) or not series:
        return None, None
    first = series[0]
    if not isinstance(first, str) or not first.strip():
        return None, None
    m = _SERIES_RE.match(first.strip())
    if m:
        return m.group("name").strip() or None, to_float(m.group("num"))
    return first.strip(), None


def read_datafile_sidecar(folder: Path) -> DatafileSidecar | None:
    """Read `folder/metadata.json` into a DatafileSidecar, or None if absent/invalid."""
    path = folder / "metadata.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"unreadable sidecar at {path}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    series_name, series_sequence = _parse_series(data.get("series"))
    return DatafileSidecar(
        title=_str_or_none(data.get("title")),
        subtitle=_str_or_none(data.get("subtitle")),
        authors=[a for a in (data.get("authors") or []) if isinstance(a, str)],
        narrators=[n for n in (data.get("narrators") or []) if isinstance(n, str)],
        series_name=series_name,
        series_sequence=series_sequence,
        publish_year=year_or_none(data.get("publishedYear")),
        publisher=_str_or_none(data.get("publisher")),
        description=_str_or_none(data.get("description")),
        asin=_str_or_none(data.get("asin")),
        isbn=_str_or_none(data.get("isbn")),
    )


def is_container_datafile(
    datafile: DatafileSidecar, folder: Path, content_kind: ContentKind
) -> bool:
    """True when the folder's metadata.json describes the container (a MULTI
    folder) rather than a book: title == folder name and the sole author == the
    parent (uploader) folder. Such a datafile must not seed a single BookUnit."""
    if content_kind is not ContentKind.MULTI:
        return False
    parent = folder.parent
    if not parent.name:  # root-level: no uploader/parent folder to match
        return False
    title = (datafile.title or "").strip()
    return title == folder.name and datafile.authors == [parent.name]


def _format_sequence(sequence: float | None) -> str:
    if sequence is None:
        return ""
    return str(int(sequence)) if sequence == int(sequence) else str(sequence)


def _series_strings(book: BookUnit) -> list[str]:
    out: list[str] = []
    for s in book.series:
        num = _format_sequence(s.sequence)
        out.append(f"{s.name} #{num}" if num else s.name)
    return out


def write_datafile_sidecar(folder: Path, book: BookUnit) -> None:
    """Merge `book`'s managed fields into `folder/metadata.json`, atomically.

    Existing keys not managed by Colophon (genres, chapters, tags, etc.) are
    preserved. The file is written to a temp path then atomically renamed.
    """
    path = folder / "metadata.json"
    existing: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}  # corrupt/unreadable: start fresh, managed fields below

    existing.update(
        {
            "title": book.title,
            "subtitle": book.subtitle,
            "authors": list(book.authors),
            "narrators": list(book.narrators),
            "series": _series_strings(book),
            "publishedYear": str(book.publish_year) if book.publish_year is not None else None,
            "publisher": book.publisher,
            "description": book.description,
            "asin": book.asin,
            "isbn": book.isbn,
        }
    )

    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{path.name}.tmp"
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
