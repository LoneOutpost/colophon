"""The single LazyLibrarian-style $Token vocabulary, shared by the filename parser,
the organize-path renderer, and the Settings help. One table so the three can't drift."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    name: str            # the part after "$", e.g. "Author"
    field: str | None    # BookUnit field this parses into; None = not parseable / $Skip
    parses: bool         # may appear in a filename_template (parse) pattern
    builds: bool         # may appear in an organize (build) pattern
    description: str      # human help, rendered in Settings
    hidden: bool = False  # still renders, but omitted from the Settings token reference
    pattern: str = ".+?"  # regex fragment this token captures when parsing; numeric tokens
    #                       constrain it so a digit inside neighbouring text isn't mis-captured


# Display order in Settings. The build-only names below must exactly equal the keys
# core/pathscheme._token_values produces (the pathscheme guard asserts this).
TOKENS: list[Token] = [
    Token("Author", "author", True, True, "First author."),
    Token("Title", "title", True, True, "Book title."),
    Token("Narrator", "narrator", True, True, "First narrator."),
    # $Series parses the raw series name but builds the composed series label (Series
    # Pattern); $SerName is the raw name in build patterns.
    Token("Series", "series", True, True, "Build: composed series label (Series Pattern), e.g. '(Stormlight Archive Book #1)'."),
    Token("SerNum", "sequence", True, True, "Series sequence number.", pattern=r"\d+(?:\.\d+)?"),
    Token("PubYear", "year", True, True, "Publication year.", pattern=r"\d{4}"),
    Token("Skip", None, True, False, "Parse only: match and discard a run of text."),
    Token("SortAuthor", None, False, True, "Build only: author as 'Last, First'."),
    Token("SortTitle", None, False, True, "Build only: title with a leading article dropped."),
    Token("Franchise", None, False, True, "Build only: the franchise this book belongs to (e.g. 'Star Trek'). Wrap it in an optional group like [$Franchise/] so the folder drops when the book has no franchise."),
    Token("SerName", None, False, True, "Build only: raw series name."),
    Token("FmtName", None, False, True, "Build only: formatted series name (Series Name Pattern)."),
    Token("FmtNum", None, False, True, "Build only: formatted series number (Series Number Pattern), e.g. 'Book #3'."),
    Token("Language", None, False, True, "Build only: book language."),
    Token("PadNum", None, False, True, "Build only: $SerNum zero-padded to two digits."),
    Token("Part", None, False, True, "Build only: multi-part index, padded to $Total's width; empty for single-file books."),
    Token("Total", None, False, True, "Build only: multi-part total; empty for single-file books."),
    Token("Abridged", None, False, True, "Build only: 'Abridged' or 'Unabridged'."),
]

PARSE_TOKENS: list[Token] = [t for t in TOKENS if t.parses]
BUILD_TOKENS: list[Token] = [t for t in TOKENS if t.builds]
# Keyed by lowercased name so lookups are case-insensitive ($SKIP == $Skip == $skip).
# The token names are all distinct case-insensitively, so no entry is lost.
_BY_NAME: dict[str, Token] = {t.name.lower(): t for t in TOKENS}


def token_by_name(name: str) -> Token | None:
    return _BY_NAME.get(name.lower())


def parse_field_for(name: str) -> str | None:
    """The model field a parseable token captures, or None for build-only/$Skip/unknown."""
    tok = _BY_NAME.get(name.lower())
    return tok.field if (tok is not None and tok.parses) else None


# %placeholder% -> $Token migration. %subtitle% was a no-op capture, so it becomes a
# discard ($Skip) to preserve the surrounding match rather than gluing literals together.
_PERCENT_TO_TOKEN: dict[str, str] = {
    "author": "Author", "title": "Title", "narrator": "Narrator", "series": "Series",
    "sequence": "SerNum", "year": "PubYear", "skip": "Skip", "subtitle": "Skip",
}
_PERCENT = re.compile(r"%(\w+)%")


def migrate_filename_template(template: str) -> str:
    """Convert any %placeholder% to its $Token equivalent. Idempotent (a $Token string
    has no %...% to touch); an unrecognized %x% is left verbatim so nothing is lost."""
    return _PERCENT.sub(
        lambda m: f"${_PERCENT_TO_TOKEN[m.group(1)]}" if m.group(1) in _PERCENT_TO_TOKEN
        else m.group(0),
        template,
    )


_BARE_DIR_FIELD = {"author": "Author", "series": "Series", "title": "Title"}


def migrate_directory_scheme(spec: str) -> str:
    """Convert a legacy bare directory scheme ('Author/Series/Title') to $Token form.
    A bare known field becomes its token; any other bare level becomes $Skip (ignore that
    level). A scheme already containing '$' is returned unchanged (idempotent)."""
    if not spec or "$" in spec:
        return spec
    out: list[str] = []
    for level in spec.split("/"):
        name = level.strip()
        if not name:
            continue
        tok = _BARE_DIR_FIELD.get(name.lower())
        out.append(f"${tok}" if tok else "$Skip")
    return "/".join(out)
