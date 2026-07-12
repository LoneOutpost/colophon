"""Field-scoped free-text search over books.

The library filter accepts a hybrid query: bare terms match the whole book
(today's behavior), while `field:value` tokens scope a term to one field. Every
condition is AND'd. A small builder in the UI writes this same syntax into the
one filter input, so the string stays the single source of truth (the URL
`?filter=` carries it verbatim).

This module is pure: it never touches the controller or the graph. Callers pass
in the two pieces that are not on the model — the book's filename and the
precomputed "any field" haystack — so the matcher stays trivially testable.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from colophon.core.models import BookUnit

# Canonical token -> human label, in dropdown order. `any` is the whole-haystack
# match (today's behavior); it is stored as a bare term (Condition.field is None).
FIELDS: list[tuple[str, str]] = [
    ("any", "Any field"),
    ("title", "Title"),
    ("subtitle", "Subtitle"),
    ("author", "Author"),
    ("narrator", "Narrator"),
    ("series", "Series"),
    ("franchise", "Franchise"),
    ("publisher", "Publisher"),
    ("genre", "Genre"),
    ("tag", "Tag"),
    ("filename", "Filename"),
    ("asin", "ASIN"),
    ("isbn", "ISBN"),
    ("year", "Year"),
    ("language", "Language"),
    ("description", "Description"),
]

_FIELD_LABELS = dict(FIELDS)
# Recognized scoping prefixes (excludes `any`, which resolves to a bare term).
_SCOPED_FIELDS = {token for token, _ in FIELDS if token != "any"}


@dataclass(frozen=True)
class Condition:
    """One filter clause. `field is None` means an any-field (bare) term; the
    value is always lowercased at parse time."""

    field: str | None
    value: str


def field_label(token: str) -> str:
    """Human label for a canonical field token (falls back to the token)."""
    return _FIELD_LABELS.get(token, token)


def parse_query(text: str) -> list[Condition]:
    """Parse a filter string into AND'd conditions.

    Bare words become any-field terms; `field:value` becomes a scoped condition
    when the prefix is a known field. Values may be quoted to include whitespace.
    An unbalanced quote degrades to a plain whitespace split rather than raising.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()

    conditions: list[Condition] = []
    for token in tokens:
        prefix, sep, value = token.partition(":")
        if sep:
            key = prefix.strip().lower()
            if key == "any" and value:
                conditions.append(Condition(None, value.lower()))
                continue
            if key in _SCOPED_FIELDS:
                if value:  # `author:` with no value contributes nothing
                    conditions.append(Condition(key, value.lower()))
                continue
        # Not a known field prefix: treat the whole token as a bare term.
        conditions.append(Condition(None, token.lower()))
    return conditions


def format_token(field: str, value: str) -> str:
    """Render one `field:value` token, quoting the value when it has whitespace."""
    value = value.strip()
    if any(ch.isspace() for ch in value):
        value = f'"{value}"'
    return f"{field}:{value}"


def format_query(conditions: list[Condition]) -> str:
    """Render conditions back into a query string (inverse of `parse_query`)."""
    parts: list[str] = []
    for cond in conditions:
        if cond.field is None:
            parts.append(f'"{cond.value}"' if " " in cond.value else cond.value)
        else:
            parts.append(format_token(cond.field, cond.value))
    return " ".join(parts)


def _field_haystack(book: BookUnit, field: str, filename: str) -> str:
    """Lowercased searchable text for a single field. Multi-value fields are
    joined so a substring match hits any element."""
    if field == "title":
        return (book.title or "").lower()
    if field == "subtitle":
        return (book.subtitle or "").lower()
    if field == "author":
        return "; ".join(book.authors).lower()
    if field == "narrator":
        return "; ".join(book.narrators).lower()
    if field == "series":
        return "; ".join(s.name for s in book.series).lower()
    if field == "franchise":
        return (book.franchise or "").lower()
    if field == "publisher":
        return (book.publisher or "").lower()
    if field == "genre":
        return "; ".join(book.genres).lower()
    if field == "tag":
        return "; ".join(book.tags).lower()
    if field == "filename":
        return filename.lower()
    if field == "asin":
        return (book.asin or "").lower()
    if field == "isbn":
        return (book.isbn or "").lower()
    if field == "language":
        return (book.language or "").lower()
    if field == "description":
        return (book.description or "").lower()
    return ""


def book_matches(
    book: BookUnit,
    conditions: list[Condition],
    *,
    filename: str,
    any_haystack: str,
) -> bool:
    """True when the book satisfies every condition (AND).

    `any_haystack` is the lowercased whole-book haystack used for bare terms;
    `filename` backs the `filename:` field. Both come from the caller so this
    module stays free of controller/graph dependencies. Year matches exactly on
    the 4-digit value; every other field is a case-insensitive substring.
    """
    for cond in conditions:
        if cond.field is None:
            if cond.value not in any_haystack:
                return False
        elif cond.field == "year":
            year = str(book.publish_year) if book.publish_year is not None else ""
            if cond.value != year:
                return False
        elif cond.value not in _field_haystack(book, cond.field, filename):
            return False
    return True
