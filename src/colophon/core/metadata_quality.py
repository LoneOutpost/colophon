"""Detectors for junk metadata values — the shared 'is this a real title/author, or rip garbage?'
toolbox. Pure predicates with no process logic; any process (scan clustering, title corroboration,
field repair, match) composes them so 'what counts as junk' has ONE definition.

See src/colophon/core/README.md for the full identity-tool catalog."""

from __future__ import annotations

import re

from colophon.core.normalize import collides_with_title
from colophon.core.number_pair import extract_enumeration
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
# A bare structural marker word — "Chapter", "Part", "Disc" — with or without a trailing number.
# Catches a marker used verbatim as an identity (e.g. a "Chapter" series), which the numbered
# _PLACEHOLDER/_TRACK_PREFIX patterns miss.
_MARKER_WORD = re.compile(
    r"^(?:track|disc|disk|cd|chapter|chap|volume|vol|part|side)\s*\d*$", re.IGNORECASE)


def is_placeholder_title(value: str | None) -> bool:
    """A generic rip placeholder ("Track 3", "Unknown Album", "Untitled") — never a real title."""
    return bool(value) and bool(_PLACEHOLDER.match(value.strip()))


def is_index_title(value: str | None) -> bool:
    """A pure index used as a title: a bare number ("15") or a track-of-total ("01 of 15")."""
    if not value:
        return False
    v = value.strip()
    return bool(_BARE_NUM_TITLE.match(v)) or bool(_INDEX_OF.match(v))


def is_structural_marker(value: str | None) -> bool:
    """A positional/structural marker, not an identity value: blank, a rip placeholder ("Track 3",
    "Unknown Album"), a leading track/chapter marker ("Track 007 - Opening"), a bare/track-of-total
    index ("15", "01 of 15"), or a bare marker word ("Chapter", "Part"). The universal gate wherever a
    tag value would name an IDENTITY (work-grouping, series, title, author). A structural marker still
    carries sequence/part info, so callers that derive sequence read it; this only bars it from naming
    an identity."""
    if not value or not value.strip():
        return True
    v = value.strip()
    return (is_placeholder_title(v) or bool(_TRACK_PREFIX.match(v))
            or is_index_title(v) or bool(_MARKER_WORD.match(v)))


def is_junk_title(value: str | None) -> bool:
    """Umbrella check that `value` is not a usable book title — delegates to `is_structural_marker`."""
    return is_structural_marker(value)


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


# --- Scalar, field-relative junk magnitude -----------------------------------------------------
# A candidate's ballot weight is scaled by (1 - junk): a scalar, not a veto. Junk here means the token
# is structurally WRONG for the field on its own account (intrinsic); it is deliberately separate from
# losing an election to a stronger candidate (relational — the field engine's winner-exclusion). A
# value can be clean for one field and junk for another, so the scorers are per-field.
_SEGMENT_SEP = re.compile(r"\.\s*-\s*\.")                    # a ".-." folder separator smuggled in whole
_BARE_PAREN_NUM = re.compile(r"^\s*[(\[]\s*\d{1,3}\s*[)\]]\s*$")            # "(5)", "[12]"
# A bracketed "series" label ("Acorna (Series) r", "[Series]") — a series folder's name, never an
# author. High precision: the bracket convention is unambiguous, so a real author is not suppressed.
_SERIES_LABEL = re.compile(r"[(\[]\s*series\b", re.IGNORECASE)
_LEADING_INDEX = re.compile(r"^\s*\d{1,3}(?!\d)\s*[-_.)\]]")               # "07-", "01_", "18 -"; not "2001 -"


def _has_enum_pair(value: str) -> bool:
    """True when an explicit "N of M" / "N/M" enumeration pair is embedded — the regex-utility signal,
    never a lone number. Underscores are folded first so "…_1_of_8" reads as "… 1 of 8"."""
    _, pairs = extract_enumeration(value.replace("_", " "))
    return bool(pairs)


def author_junk(value: str | None) -> float:
    """Scalar [0,1] junk magnitude for an AUTHOR candidate. HIGH for a structural marker, a value that
    spans a ".-." segment separator (a whole `Author.-.Title` string), a title-shaped author, or a
    value carrying an embedded enumeration pair ("1 of 8 Diana Gabaldon"). A lone number (a year,
    "Top 100", "E. E. (Doc) Smith") is NOT junk — the pair is the signal, not the digit."""
    if not value or not value.strip():
        return 1.0
    v = value.strip()
    if (is_structural_marker(v) or is_title_shaped_author(v) or _SEGMENT_SEP.search(v)
            or _BARE_PAREN_NUM.match(v) or _SERIES_LABEL.search(v)):
        return 1.0
    if _has_enum_pair(v):
        return 0.9
    return 0.0


def title_junk(value: str | None) -> float:
    """Scalar [0,1] junk magnitude for a TITLE candidate. HIGH for a structural marker, a bare
    parenthesized number ("(5)"), a ".-."-spanning value, or an embedded enumeration pair; MODERATE
    for a leading small-index prefix ("07-Foundation", "18 - Childhood's End"). A 4-digit-led or prose
    title ("2001 - A Space Odyssey") scores ~0 — the `_INT` guard keeps the year from reading as an
    index."""
    if not value or not value.strip():
        return 1.0
    v = value.strip()
    if is_structural_marker(v) or _BARE_PAREN_NUM.match(v) or _SEGMENT_SEP.search(v):
        return 1.0
    if _has_enum_pair(v):
        return 0.8
    if _LEADING_INDEX.match(v):
        return 0.7
    return 0.0
