"""Extract the title token(s) from an identity source (folder name / filename) by tokenizing it and
removing the tokens that play OTHER roles — a known author or series (matched with normalize_key, which
folds initials/punctuation/PascalCase so 'HP Lovecraft' == 'H. P. Lovecraft'), a `Bk N`/`Book N`/`Vol
N` marker, and structural/index/bracketed tokens. General and structural: roles are identified by
cross-reference and markers, never by a corpus-specific encoding suffix. See
tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

import re

from colophon.core.cohort_constancy import _tokens
from colophon.core.metadata_quality import is_structural_marker
from colophon.core.normalize import normalize_key
from colophon.core.people import looks_like_person_name
from colophon.core.sequence_affix import strip_encoding_artifact

# A series marker: 'Book/Vol/Volume N' need a word boundary (so 'Textbook 1' is not a marker), but the
# 'bk' abbreviation is matched even when glued ('P&FBk 12', 'Flinx Bk12') — no English word ends in a
# 'bk' followed by a digit, so no boundary is needed and 'Textbook'/'Notebook' stay safe (they carry
# 'book', not 'bk').
_SERIES_MARKER = re.compile(r"(?:\bbook|\bvol|\bvolume|bk)\s*\d", re.IGNORECASE)

# A token that is NOTHING but an index or disc marker ('1/9', '01-12', '001 of 153', 'CD01') — never a
# title, drop it whole.
_TOK_PURE_INDEX = re.compile(
    r"^\s*(?:cd|disc|disk|dvd)?\s*\.?\s*\d{1,3}(?:\s*(?:of|/|[-–])\s*\d{1,3})?\s*$", re.IGNORECASE)  # noqa: RUF001
# A leading disc/CD marker or a padded / 'N of M' index prefix glued to the title ('CD01 Some Title',
# '01 The Coming') — strip it, keep the title after.
_TOK_LEAD_MARK = re.compile(
    r"^(?:(?:cd|disc|disk|dvd)\s*\.?\s*\d+|0\d{1,2}(?!\d)|\d{1,3}(?!\d)\s*(?:of|/)\s*\d{1,3})[\s.\-_]+(?=\S)",
    re.IGNORECASE)
# A trailing padded index / N-N range / disc marker — strip it (NOT a bare unpadded number, which is
# part of the title: 'Slaughterhouse 5').
_TOK_TRAIL_MARK = re.compile(
    r"[\s.\-_]+(?:(?:cd|disc|disk|dvd)\s*\.?\s*\d+(?:\s*(?:of|[-./])\s*\d+)?"
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
    """The author declared by a leaf book folder that uses the `.-.` convention
    (`Author.-.[Series BkNN.-.]Title`): its FIRST `.-.` segment, but only when that segment is
    person-name-shaped and not a staging/format word (the guard against a folder that isn't
    `Author.-.…`). Returns None for a folder with no `.-.` or a non-name first segment. The library's
    leaf export convention puts the author first; a folder with a dedicated author-node ancestor already
    gets that author from the graph, so this only supplies the author the depth logic misses when the
    author lives inside the leaf name."""
    if ".-." not in folder_name:
        return None
    head = folder_name.split(".-.", 1)[0].strip()
    if head.casefold() in _LEAF_NON_AUTHOR:
        return None
    return head if looks_like_person_name(head) else None


def title_candidates(name: str, *, authors: list[str], series: list[str]) -> list[str]:
    """The title token(s) in `name`: its ` - ` / `.-.` segments, each cleaned of index/disc affixes,
    minus tokens playing other roles.

    Author/series removal MAY return [] (an author-only folder has no title here); structural removal
    is skipped when it would remove the last token, so a bare-number title like "1984" survives."""
    akeys = {normalize_key(a) for a in authors}
    if len(authors) > 1:                        # also drop the JOINED author form, not just each name
        akeys.update(normalize_key(sep.join(authors)) for sep in (" & ", " and ", ", "))
    skeys = {normalize_key(s) for s in series}
    kept: list[str] = []
    for raw in _tokens(name):
        if raw.startswith(("(", "[")):
            continue
        t = _clean_token(raw)
        if t is None or normalize_key(t) in akeys or normalize_key(t) in skeys or _SERIES_MARKER.search(t):
            continue
        kept.append(t)
    if not kept:
        return []
    non_structural = [t for t in kept if not is_structural_marker(t)]
    return non_structural if non_structural else kept
