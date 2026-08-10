"""Detectors for junk metadata values — the shared 'is this a real title/author, or rip garbage?'
toolbox. Pure predicates with no process logic; any process (scan clustering, title corroboration,
field repair, match) composes them so 'what counts as junk' has ONE definition.

See src/colophon/core/README.md for the full identity-tool catalog."""

from __future__ import annotations

import re

from colophon.core.normalize import collides_with_title
from colophon.core.sequence_affix import parse_sequence_affix

# Generic placeholder tag values a rip leaves in: "Track 3", "Disc 1", "CD 2", "Chapter 5",
# "Volume 1", "Unknown Album …", "Untitled".
_PLACEHOLDER = re.compile(
    r"^(?:(?:track|disc|cd|chapter|volume|vol|part)\s*\d+|unknown(?:\s.*)?|untitled)$", re.IGNORECASE
)
# A leading per-file structural marker ("Track 001 - Opening Theme", "Disk 2 …"): a track/chapter
# label, not a book title, even with trailing text the anchored placeholder wouldn't catch.
_TRACK_PREFIX = re.compile(
    r"^\s*(?:track|disc|disk|cd|chapter|chap|volume|vol|part|side)\s*\d", re.IGNORECASE
)
_BARE_NUM_TITLE = re.compile(r"^\d{1,4}$")
_INDEX_OF = re.compile(r"^\d{1,3}\s+of\s+\d{1,3}$", re.IGNORECASE)   # "01 of 15" track-of-total
# A series-book number stuffed into an author value: "... (Flinx 03)", "... (The Expanse #4)".
_SERIES_PAREN = re.compile(r"\(\s*.+?#?\s*\d", re.IGNORECASE)


def is_placeholder_title(value: str | None) -> bool:
    """A generic rip placeholder ("Track 3", "Unknown Album", "Untitled") — never a real title."""
    return bool(value) and bool(_PLACEHOLDER.match(value.strip()))


def is_index_title(value: str | None) -> bool:
    """A pure index used as a title: a bare number ("15") or a track-of-total ("01 of 15")."""
    if not value:
        return False
    v = value.strip()
    return bool(_BARE_NUM_TITLE.match(v)) or bool(_INDEX_OF.match(v))


def is_junk_title(value: str | None) -> bool:
    """The umbrella check: `value` is not a usable book title — empty, a placeholder, a leading
    track/chapter marker, or a bare/track-of-total index."""
    if not value or not value.strip():
        return True
    v = value.strip()
    return is_placeholder_title(v) or bool(_TRACK_PREFIX.match(v)) or is_index_title(v)


def is_title_shaped_author(author: str | None, title: str | None = None) -> bool:
    """An author value that is really a title: it carries a series-book parenthetical ("(Flinx 03)")
    or a strong sequence affix, or it simply echoes the book's title."""
    if not author:
        return False
    if _SERIES_PAREN.search(author):
        return True
    if parse_sequence_affix(author) is not None:
        return True
    return bool(title and collides_with_title(author, title))
