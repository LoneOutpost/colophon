"""Uniform string get/set accessors over BookUnit's typed editable fields.

Enables generic edit/remap logic (ported from id3editor's operations.py) without
hard-coding each field's shape. List fields join/split on '; '; series/sequence
address the first SeriesRef; year maps to publish_year.
"""

from __future__ import annotations

from colophon.core.coerce import to_float, to_int
from colophon.core.models import BookUnit, SeriesRef

EDITABLE_FIELDS = [
    "title", "subtitle", "author", "narrator", "series",
    "sequence", "year", "asin", "language", "publisher", "description",
]

_SCALARS = {"title", "subtitle", "asin", "language", "publisher", "description"}


def _check(field: str) -> None:
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"unknown editable field {field!r}")


def get_field(book: BookUnit, field: str) -> str | None:
    _check(field)
    if field in _SCALARS:
        return getattr(book, field)
    if field == "author":
        return "; ".join(book.authors) or None
    if field == "narrator":
        return "; ".join(book.narrators) or None
    if field == "series":
        return book.series[0].name if book.series else None
    if field == "sequence":
        if book.series and book.series[0].sequence is not None:
            return str(book.series[0].sequence)
        return None
    if field == "year":
        return str(book.publish_year) if book.publish_year is not None else None
    return None  # unreachable


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def set_field(book: BookUnit, field: str, value: str | None) -> None:
    _check(field)
    if field in _SCALARS:
        setattr(book, field, value or None)
    elif field == "author":
        book.authors = _split(value)
    elif field == "narrator":
        book.narrators = _split(value)
    elif field == "series":
        if value:
            seq = book.series[0].sequence if book.series else None
            book.series = [SeriesRef(name=value, sequence=seq)]
        else:
            book.series = []
    elif field == "sequence":
        # Setting sequence when book.series is empty is intentionally a no-op
        # (a sequence with no series name is meaningless; set series first).
        if book.series:
            book.series[0].sequence = to_float(value)
    elif field == "year":
        book.publish_year = to_int(value)
