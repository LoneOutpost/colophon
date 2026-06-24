"""Normalize messy field values for display/storage.

`normalize_text` title-cases short fields and fixes separators/commas;
`normalize_description` cleans HTML/entities in prose without title-casing. Both
are pure and conservative — they reshape obviously-wrong formatting, not meaning.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

# Words kept lowercase in title case (except as the first/last word or after a colon):
# articles, coordinating conjunctions, and short prepositions.
_SMALL_WORDS = {
    "a", "an", "the",
    "and", "or", "but", "nor", "for", "yet", "so",
    "of", "in", "on", "at", "to", "by", "up", "as", "off", "per", "via", "from", "into", "with", "over",
    "vs", "vs.",
}


_DASH_RE = re.compile(r"\s*-\s*")
_COMMA_RE = re.compile(r"\s*,\s*")
_WS_RE = re.compile(r"\s+")


def _cap(word: str) -> str:
    """Capitalize the first character and lowercase the rest (fixes ALL-CAPS input)."""
    return word[:1].upper() + word[1:].lower()


def _titlecase(text: str, *, lowercase_small: bool = True) -> str:
    """Title-case `text`. When `lowercase_small` is False, small words (articles,
    conjunctions, short prepositions, and single letters like the initial "A") are
    NOT lowercased; every word is capitalized. Use that for person names, where
    initials and particles must keep their case."""
    words = text.split(" ")
    last = len(words) - 1
    out: list[str] = []
    cap_next = True  # first word is always capitalized
    for i, word in enumerate(words):
        if not word:
            out.append(word)
            continue
        if not lowercase_small or cap_next or i == last or word.lower() not in _SMALL_WORDS:
            out.append(_cap(word))
        else:
            out.append(word.lower())
        # Capitalize the first word after a colon or a dash separator.
        cap_next = word.endswith(":") or word == "-"
    return " ".join(out)


def normalize_text(value: str, *, lowercase_small: bool = True) -> str:
    """Title-case + separator/comma cleanup for short text fields. Set
    `lowercase_small=False` for names (see `normalize_name`) so short words and
    single-letter initials keep their capitalization."""
    s = value.strip()
    if not s:
        return ""
    s = s.replace("_", " ")
    # A hyphen with adjacent whitespace is a dash separator -> " - "; a bare
    # hyphen (kebab joiner) -> space. (Note: this splits hyphenated words like
    # "well-known"; an exceptions list could be added later.)
    s = _DASH_RE.sub(lambda m: " - " if m.group(0) != "-" else " ", s)
    s = _COMMA_RE.sub(", ", s)             # no space before a comma, one after
    s = _WS_RE.sub(" ", s).strip()         # collapse runs of whitespace
    return _titlecase(s, lowercase_small=lowercase_small)


def normalize_name(value: str) -> str:
    """Normalize a person name: same cleanup as `normalize_text` but without
    lowercasing small words, so initials like "A" and particles stay capitalized
    (for example 'john a smith' -> 'John A Smith')."""
    return normalize_text(value, lowercase_small=False)


def normalize_description(value: str) -> str:
    """Clean common HTML/entity cruft from prose (not full HTML): line-break tags
    become newlines, other tags are stripped, entities are decoded, comma spacing
    is fixed, and excess blank lines collapse. Title-case is NOT applied."""
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)      # <br>, <br/> -> newline
    s = re.sub(r"(?i)</?\s*(?:p|div)[^>]*>", "\n", s)     # <p>/</p>/<div>/</div> -> newline
    s = re.sub(r"<[^>]+>", "", s)                          # strip any remaining tags
    s = html.unescape(s).replace("\xa0", " ")              # decode entities; nbsp -> space
    s = re.sub(r"[ \t]*,[ \t]*", ", ", s)                  # comma spacing (not across newlines)
    s = re.sub(r"[ \t]+\n", "\n", s)                       # drop line-trailing spaces
    s = re.sub(r"\n{3,}", "\n\n", s)                       # collapse blank-line runs
    return s.strip()


def dedupe_normalized(
    values: list[str],
    *,
    transform: Callable[[str], str] | None = None,
    keep: Callable[[str], bool] | None = None,
) -> list[str]:
    """Title-case each value via normalize_text (after an optional `transform`),
    drop blanks, optionally keep only values whose casefold passes `keep`, and dedupe
    case-insensitively preserving first-seen order. Shared by `normalize_genres` and
    `GenrePolicy.canonicalize` (mapping = `transform`, whitelist = `keep`)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = transform(raw) if transform is not None else raw
        name = normalize_text(item or "").strip()
        if not name:
            continue
        fold = name.casefold()
        if keep is not None and not keep(fold):
            continue
        if fold in seen:
            continue
        seen.add(fold)
        out.append(name)
    return out


def normalize_genres(values: list[str]) -> list[str]:
    """Title-case each genre via normalize_text, dropping blanks, and dedupe
    case-insensitively while preserving first-seen order. For synonym mapping and an
    optional accepted-genres whitelist on top of this, see GenrePolicy.canonicalize."""
    return dedupe_normalized(values)


def merge_preserve(existing: list[str], new: list[str]) -> list[str]:
    """Union of two string lists: `existing` first, then `new`, exact-string
    dedupe preserving first-seen order, dropping blanks. Used to merge tags
    (fluid, no case-folding)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *new]:
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_genre_field(value: str) -> str:
    """Normalize a '; '-joined genre string (the FIELD_NORMALIZERS adapter)."""
    parts = [p.strip() for p in value.split(";")]
    return "; ".join(normalize_genres(parts))


# Editable fields that hold free text worth normalizing, mapped to the normalizer
# that applies. Numeric/code fields (year, sequence, asin, language) are excluded.
FIELD_NORMALIZERS = {
    "title": normalize_text,
    "subtitle": normalize_text,
    "author": normalize_name,
    "narrator": normalize_name,
    "series": normalize_text,
    "publisher": normalize_text,
    "genre": _normalize_genre_field,
    "description": normalize_description,
}

NORMALIZABLE_FIELDS = list(FIELD_NORMALIZERS)
