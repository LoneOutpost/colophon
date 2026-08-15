"""Extract the title token(s) from an identity source (folder name / filename) by tokenizing it and
removing the tokens that play OTHER roles — a known author or series (matched with normalize_key, which
folds initials/punctuation/PascalCase so 'HP Lovecraft' == 'H. P. Lovecraft'), a `Bk N`/`Book N`/`Vol
N` marker, and structural/index/bracketed tokens. General and structural: roles are identified by
cross-reference and markers, never by a corpus-specific encoding suffix. See
tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

import re

from colophon.core.cohort_constancy import _tokens, separator_segments
from colophon.core.metadata_quality import is_structural_marker
from colophon.core.normalize import normalize_key
from colophon.core.people import looks_like_person_name
from colophon.core.sequence_affix import strip_encoding_artifact

# A series marker: 'Book/Vol/Volume N' need a word boundary (so 'Textbook 1' is not a marker), but the
# 'bk' abbreviation is matched even when glued ('P&FBk 12', 'Flinx Bk12') — no English word ends in a
# 'bk' followed by a digit, so no boundary is needed and 'Textbook'/'Notebook' stay safe (they carry
# 'book', not 'bk').
_SERIES_MARKER = re.compile(r"(?:\bbook|\bvol|\bvolume|bk)\s*\d", re.IGNORECASE)
# A bare `Name NN` series reference (no `Bk`/`#`): a letter-led segment ending in a space + a 1-3
# digit number ('Eve Dallas 11', 'Renegades of Pern 08', 'Warlord 1'). Only DROPPED as a series
# prefix when a plainer title segment sits beside it; a lone one ('Slaughterhouse 5', 'Apollo 13') is
# kept as the title. A digit-led title ('2001 A Space Odyssey') never matches (must start with a letter).
_BARE_SERIES_REF = re.compile(r"^[^\W\d].*\s\d{1,3}$")

# A token that is NOTHING but an index or disc/part marker ('1/9', '01-12', '001 of 153', 'CD01',
# 'Part 3') — never a title, drop it whole.
_TOK_PURE_INDEX = re.compile(
    r"^\s*(?:cd|disc|disk|dvd|part|pt)?\s*\.?\s*\d{1,3}(?:\s*(?:of|/|[-–])\s*\d{1,3})?\s*$", re.IGNORECASE)  # noqa: RUF001
# A leading disc/CD/part marker or a padded / 'N of M' index prefix glued to the title ('CD01 Some
# Title', '01 The Coming') — strip it, keep the title after.
_TOK_LEAD_MARK = re.compile(
    r"^(?:(?:cd|disc|disk|dvd|part|pt)\s*\.?\s*\d+|0\d{1,2}(?!\d)|\d{1,3}(?!\d)\s*(?:of|/)\s*\d{1,3})[\s.\-_]+(?=\S)",
    re.IGNORECASE)
# A trailing padded index / N-N range / disc / part marker — strip it (NOT a bare unpadded number,
# which is part of the title: 'Slaughterhouse 5'). 'Dark Jenny-Part01' -> 'Dark Jenny' so the cohort
# can see the constant title under a glued per-file part index.
_TOK_TRAIL_MARK = re.compile(
    r"[\s.\-_]+(?:(?:cd|disc|disk|dvd|part|pt)\s*\.?\s*\d+(?:\s*(?:of|[-./])\s*\d+)?"
    r"|0\d{1,2}(?!\d)|\d{1,3}[-/]\d{1,3})\s*$", re.IGNORECASE)


# A bare/trailing abridged-edition marker ('Unb', 'UA', 'UA 1-64.44m') — encoding noise, strip it.
_TOK_ENCODING = re.compile(
    r"(?i)\b(?:ua|unb|una|unabr(?:idged)?|abridged)\b\s*\d*\s*(?:[@\-]\s*[\w.]+)?")


def _clean_token(tok: str) -> str | None:
    """Clean a title token: unwrap a curly series-ref fragment (the title follows the closing brace),
    strip encoding artifacts and a leading/trailing index or disc marker. Returns None if nothing but a
    marker is left. A bare unpadded number or a 4-digit year survives (part of the title)."""
    if "}" in tok:                      # '{Arcane Society #1} Second Sight' -> 'Second Sight'
        tok = tok.rsplit("}", 1)[-1]
    elif "{" in tok:                     # a lone '{Lavinia Lake' fragment -> the ref, keep nothing
        tok = tok.split("{", 1)[0]
    tok = _TOK_ENCODING.sub(" ", strip_encoding_artifact(tok))
    if _TOK_PURE_INDEX.match(tok):
        return None
    t = _TOK_TRAIL_MARK.sub("", _TOK_LEAD_MARK.sub("", tok))
    t = " ".join(t.split()).strip(" .-_")
    return t or None


# Staging/format words that head a mis-formed leaf folder ('Audiobook.-.Isaac Asimov.-.…') — a person
# name never IS one of these, so a lone such head is not the author (mirrors node_classify._BUCKET_WORDS,
# kept tiny and local to avoid an import cycle).
_LEAF_NON_AUTHOR = frozenset({"audiobook", "audiobooks", "unsorted", "misc", "media",
                              "various", "va", "unknown", "books"})


def leaf_folder_author(folder_name: str) -> str | None:
    """The author declared by a leaf name (folder OR file stem) that PACKS author + title in one string
    (`Author - [Series BkNN - ]Title`): its FIRST segment, when that segment is person-name-shaped and
    not a staging/format word. Segments split on the GENERAL separator (any non-intra-word hyphen, so
    `Author.-.Title`, `Author - Title`, and `Author_-_Title` behave identically — the `.-.` form is not
    special). Returns None for a single-segment name or a non-name first segment. A leaf with a dedicated
    author-node ancestor already gets that author from the graph; this supplies the one the depth logic
    misses when the author lives inside the leaf name."""
    segs = separator_segments(folder_name)
    if len(segs) < 2:                          # one segment packs no title -> not an Author-Title leaf
        return None
    head = segs[0]
    if head.casefold() in _LEAF_NON_AUTHOR:
        return None
    return head if looks_like_person_name(head) else None


# A series reference: 'Name [Bk|Book|Vol|Volume] [#]NN' — the name plus a 1-3 digit sequence, the
# marker word optional (so a bare 'Eve Dallas 11' parses too). Lazy name so the marker is consumed,
# not kept ('Halfblood Chronicles Bk1' -> name 'Halfblood Chronicles').
_SERIES_REF = re.compile(
    r"^(?P<name>.+?)(?:\s+(?:bk|book|vol|volume))?\s*#?\s*(?P<seq>\d{1,3})$", re.IGNORECASE)
# A bibliographic noun / sequence marker that is never itself a series name ('Book 7' -> not series
# 'Book'). `is_structural_marker` already rejects CD/Disc/Vol/Part; this covers the 'book'/'bk' words it
# leaves — the gap that let 'Bk3' / 'Book 7' parse as a series.
_NOT_SERIES_NAME = {normalize_key(w) for w in ("book", "bk", "novel", "series", "collection")}
# An 'N of M' total-count run inside the segment ('Disc 08 of 11'): a part index, not a series ref.
_OF_TOTAL = re.compile(r"\b\d{1,3}\s+of\s+\d{1,3}\b", re.IGNORECASE)


def parse_series_ref(segment: str) -> tuple[str, float] | None:
    """Parse a `Name NN` / `Name Bk NN` / `Name #NN` reference into (series name, sequence), or None.
    'Eve Dallas 11' -> ('Eve Dallas', 11.0); 'Flinx Bk03' -> ('Flinx', 3.0). The name must contain a
    letter and must not be a structural marker or bare sequence word ('Book'/'Bk'/'CD'), and an
    'N of M' part index ('Disc 08 of 11') is never a series."""
    seg = segment.strip()
    if _OF_TOTAL.search(seg):
        return None
    m = _SERIES_REF.match(seg)
    if not m:
        return None
    name = m.group("name").strip(" .-_#")
    if not name or not re.search(r"[^\W\d]", name):
        return None
    if normalize_key(name) in _NOT_SERIES_NAME or is_structural_marker(name):
        return None
    return (name, float(m.group("seq")))


def leaf_folder_series(folder_name: str) -> tuple[str, float] | None:
    """The series a leaf name (folder OR file stem) declares in a MIDDLE segment
    (`Author - Series NN - Title[ - suffix]`): the first middle segment (between the author-first and
    the title-last) that parses as a series reference. Segments split on the GENERAL separator, so the
    `.-.` form is not special. Returns None for fewer than three segments or no series-shaped middle —
    so `Author - Title` and `Author - Title - UA…` yield nothing (the title slot is never the series)."""
    segs = separator_segments(folder_name)
    if len(segs) < 3:
        return None
    for seg in segs[1:-1]:                      # skip the author (first) and the title (last)
        ref = parse_series_ref(seg)
        if ref:
            return ref
    return None


def title_candidates(name: str, *, authors: list[str], series: list[str]) -> list[str]:
    """The title token(s) in `name`: its ` - ` / `.-.` segments, each cleaned of index/disc affixes,
    minus tokens playing other roles.

    Author/series removal MAY return [] (an author-only folder has no title here); structural removal
    is skipped when it would remove the last token, so a bare-number title like "1984" survives."""
    akeys = {normalize_key(a) for a in authors}
    if len(authors) > 1:                        # also drop the JOINED author form, not just each name
        akeys.update(normalize_key(sep.join(authors)) for sep in (" & ", " and ", ", "))
    skeys = {normalize_key(s) for s in series}
    plain: list[str] = []       # clean title segments
    bare_refs: list[str] = []   # a `Name NN` segment: a series prefix WHEN a plainer segment exists,
    for raw in _tokens(name):   # else the title itself ('Slaughterhouse 5' with nothing plainer)
        if raw.startswith(("(", "[")):
            continue
        t = _clean_token(raw)
        # Author, series-KEY, and an explicit `Bk/Vol/Book N` marker are always dropped.
        if t is None or normalize_key(t) in akeys or normalize_key(t) in skeys or _SERIES_MARKER.search(t):
            continue
        # Test the bare `Name NN` shape on the RAW segment: _clean_token strips a padded trailing
        # index ('Renegades of Pern 08' -> 'Renegades of Pern'), which would hide the number.
        (bare_refs if _BARE_SERIES_REF.match(raw.strip()) else plain).append(t)
    kept = plain or bare_refs
    if not kept:
        return []
    non_structural = [t for t in kept if not is_structural_marker(t)]
    return non_structural if non_structural else kept
