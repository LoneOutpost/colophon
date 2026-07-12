"""Field-scoped free-text search over books.

The library filter accepts a hybrid query: bare terms match the whole book
(today's behavior), while `field:value` tokens scope a term to one field. Every
condition is AND'd. A condition can be negated with a leading `-`, and a
field-scoped value can offer OR-alternatives separated by commas
(`author:sanderson,jordan`). A small builder in the UI writes this same syntax
into the one filter input, so the string stays the single source of truth (the
URL `?filter=` carries it verbatim).

This module is pure: it never touches the controller or the graph. Callers pass
in the two pieces that are not on the model — the book's filename and the
precomputed "any field" haystack — so the matcher stays trivially testable.
"""

from __future__ import annotations

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
    """One filter clause.

    `field is None` means an any-field (bare) term. `values` holds one or more
    OR-alternatives (a match on any one satisfies the clause); it is a tuple so
    the condition stays hashable, and every value is lowercased at parse time.
    `negated` inverts the clause — the book must *not* match it.
    """

    field: str | None
    values: tuple[str, ...]
    negated: bool = False


def field_label(token: str) -> str:
    """Human label for a canonical field token (falls back to the token)."""
    return _FIELD_LABELS.get(token, token)


# --- quote-aware tokenizing -------------------------------------------------

def _split_unquoted(text: str, delimiters: str) -> list[str]:
    """Split `text` on any delimiter char that sits outside double quotes.

    Quote characters are kept in the returned pieces so a later stage can still
    tell a quoted delimiter from an unquoted one. An unclosed quote simply runs
    to the end of the string (no exception)."""
    pieces: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
        elif ch in delimiters and not in_quote:
            pieces.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    pieces.append("".join(buf))
    return pieces


def _partition_field(token: str) -> tuple[str, bool, str]:
    """Split a token at its first *unquoted* colon into (prefix, found, rest)."""
    in_quote = False
    for i, ch in enumerate(token):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            return token[:i], True, token[i + 1 :]
    return token, False, ""


def _strip_quotes(text: str) -> str:
    """Drop double-quote characters (values are matched literally, unquoted)."""
    return text.replace('"', "")


def _needs_quote(value: str) -> bool:
    """A value needs quoting when it contains whitespace or a comma, either of
    which would otherwise be read as structure."""
    return any(ch.isspace() for ch in value) or "," in value


def _quote_if_needed(value: str) -> str:
    return f'"{value}"' if _needs_quote(value) else value


def parse_query(text: str) -> list[Condition]:
    """Parse a filter string into AND'd conditions.

    Bare words become any-field terms; `field:value` becomes a scoped condition
    when the prefix is a known field. A leading `-` negates a token. In a
    field-scoped value, commas outside quotes separate OR-alternatives; quote a
    value to keep a literal comma or space (`author:"herbert, frank"`).
    """
    text = (text or "").strip()
    if not text:
        return []

    conditions: list[Condition] = []
    for token in _split_unquoted(text, " \t\n"):
        if not token:
            continue  # collapsed run of whitespace
        negated = False
        body = token
        if body.startswith("-") and len(body) > 1:
            negated, body = True, body[1:]

        prefix, found, value_str = _partition_field(body)
        key = prefix.lower()
        if found and (key == "any" or key in _SCOPED_FIELDS):
            # A known field prefix consumes the token even with an empty value
            # (`author:` contributes no condition rather than a stray bare term).
            values = tuple(
                v for v in (
                    _strip_quotes(part).strip().lower()
                    for part in _split_unquoted(value_str, ",")
                ) if v
            )
            if values:
                conditions.append(Condition(None if key == "any" else key, values, negated))
            continue

        # Not a known field prefix: the whole token is one literal bare term.
        value = _strip_quotes(body).strip().lower()
        if value:
            conditions.append(Condition(None, (value,), negated))
    return conditions


def format_token(field: str, values: list[str] | tuple[str, ...], *, negated: bool = False) -> str:
    """Render one token from a field and its OR-alternatives (inverse of parse)."""
    prefix = "-" if negated else ""
    body = ",".join(_quote_if_needed(v.strip()) for v in values if v.strip())
    if field == "any":
        return f"{prefix}{body}"
    return f"{prefix}{field}:{body}"


def build_token(field: str, value_text: str, *, negated: bool) -> str:
    """Turn a builder's raw (field, value text, negated) into one query token.

    For a real field, unquoted commas in the text become OR-alternatives; the
    `any` field keeps the text as one literal bare term (bare terms do not OR)."""
    value_text = value_text.strip()
    if not value_text:
        return ""
    if field == "any":
        return f"{'-' if negated else ''}{_quote_if_needed(_strip_quotes(value_text).strip())}"
    alts = [
        a for a in (
            _strip_quotes(part).strip() for part in _split_unquoted(value_text, ",")
        ) if a
    ]
    if not alts:
        return ""
    return format_token(field, alts, negated=negated)


def format_query(conditions: list[Condition]) -> str:
    """Render conditions back into a query string (inverse of `parse_query`)."""
    parts: list[str] = []
    for cond in conditions:
        field = "any" if cond.field is None else cond.field
        parts.append(format_token(field, cond.values, negated=cond.negated))
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


def _condition_satisfied(
    book: BookUnit, cond: Condition, filename: str, any_haystack: str
) -> bool:
    """Whether the book matches the clause, ignoring negation. Any OR-alternative
    matching is enough. Year compares exactly; every other field is substring."""
    if cond.field is None:
        return any(v in any_haystack for v in cond.values)
    if cond.field == "year":
        year = str(book.publish_year) if book.publish_year is not None else ""
        return any(v == year for v in cond.values)
    hay = _field_haystack(book, cond.field, filename)
    return any(v in hay for v in cond.values)


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
    module stays free of controller/graph dependencies. A negated condition
    flips its sense: the book must not match it.
    """
    for cond in conditions:
        satisfied = _condition_satisfied(book, cond, filename, any_haystack)
        if satisfied == cond.negated:  # matched-and-negated, or unmatched-and-required
            return False
    return True
