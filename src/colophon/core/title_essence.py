"""Derive a title's ESSENCE from the token union across independent sources (folder / filename-cohort
/ tag-cohort), then render it as a reduction of the committed title.

The general alternative to per-format strip rules: an author/series prefix, a part index, or a
per-file typo falls away because it is not corroborated across the sources, not because a rule
matched its shape. The render only ever DROPS committed words (in order), so it can never scramble,
add an author, or invent text. See tasks/2026-08-14-title-essence-design.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from colophon.core.identity_tokens import title_candidates
from colophon.core.people import looks_like_person_name

# Split a title string into words on whitespace AND hyphens, so 'Tar-Aiym' and 'Tar Aiym' tokenize
# identically. Rendering re-joins from the committed original, so the hyphen display is preserved.
_SPLIT = re.compile(r"[\s\-]+")
# A committed word this similar to an essence word is treated as the same word (a cross-source typo,
# e.g. 'Crome'/'Chrome', 'Weirdstone'/'Wierdstone').
_FUZZY = 0.86
# Glue tokens — connectives, articles, and the '&'/'+' symbols. Kept in the render regardless of
# corroboration (they are structural, never the noise we remove) and trimmed off the result's edges.
_GLUE = frozenset({"and", "the", "a", "an", "of", "or", "to", "in", "on", "with", "for", "at", "by",
                   "&", "+"})


def _key(word: str) -> str:
    """Alnum-casefold comparison key: "Acorna's"/'Acornas' -> 'acornas', 'Mr.' -> 'mr'."""
    return re.sub(r"[^0-9a-z]", "", word.casefold())


def _author_word_keys(authors: Iterable[str]) -> set[str]:
    """Individual author-name word keys, dropped from every source: `title_candidates` removes the
    author at the SEGMENT level (splitting on ` - `), but a source with no ` - ` (e.g. 'Arthur C
    Clarke The Fountains of Paradise') keeps it, so the author leaks into the union without this.
    Author only, never series — a series name commonly IS a title word ('Flinx in Flux')."""
    out: set[str] = set()
    for a in authors:
        for w in _SPLIT.split(a):
            k = _key(w)
            if k:
                out.add(k)
    return out


def _source_keys(strings: Iterable[str], authors: list[str], series: list[str], drop: set[str]) -> set[str]:
    """The union of title-word keys across one source's cohort of strings, each first cut by
    `title_candidates` (author/series/index dropped at the segment level), minus the author words."""
    keys: set[str] = set()
    for s in strings:
        if not s:
            continue
        for seg in title_candidates(s, authors=authors, series=series):
            for w in _SPLIT.split(seg):
                k = _key(w)
                if k and k not in drop:
                    keys.add(k)
    return keys


def _fuzzy_in(k: str, essence: set[str]) -> bool:
    return any(SequenceMatcher(None, k, e).ratio() >= _FUZZY for e in essence)


def title_essence(
    committed: str | None, *, folder_name: str, file_stems: list[str],
    tag_titles: list[str], authors: list[str], series: list[str],
) -> str | None:
    """Return a cleaned `committed` title reduced to the tokens corroborated across the folder /
    filename / tag sources, or None when there is no confident improvement (< 2 sources, or the
    essence already equals the committed title). The result is always a subset of `committed` in its
    original word order."""
    if not committed or not committed.strip():
        return None
    drop = _author_word_keys(authors)
    # An author-as-title book (the committed title IS a person name, a mis-derivation): essence cannot
    # help and the word-level author drop would mangle it ('Chuck Klosterman' -> 'Klosterman',
    # 'Anne Mccaffrey and Elizabeth Ann Scarborough' -> 'and Elizabeth ...'). A real person-name title
    # ('Jane Eyre') is equally left alone, which is correct — it needs no cleaning.
    if looks_like_person_name(committed):
        return None
    sources = [ks for ks in (
        _source_keys([folder_name], authors, series, drop),
        _source_keys(file_stems, authors, series, drop),
        _source_keys(tag_titles, authors, series, drop),
    ) if ks]
    if len(sources) < 2:
        return None
    essence = set().union(*sources)
    if not essence:
        return None

    # Render from `title_candidates(committed)`, not the raw committed words: it drops the author/
    # series/index at the SEGMENT level, so a prefix like 'Renegades of Pern 08 - ' goes even though
    # 'of'/'Pern' recur in the real title. Then keep only the words the essence corroborates (fuzzily),
    # which removes any residue segment `title_candidates` missed (an unrecognized series) while a
    # cross-source typo survives. Word text (hyphens) is preserved from the committed segment.
    base = " ".join(title_candidates(committed, authors=authors, series=series))
    if not base:
        return None
    kept: list[str] = []
    for word in base.split():
        w = word.strip(":;,")                 # a colon/comma left when its subtitle got dropped
        if not w:
            continue
        if w.casefold() in _GLUE:             # connectives/'&'/'+' are structural, always kept
            kept.append(w)
            continue
        subkeys = [k for k in (_key(sw) for sw in _SPLIT.split(w)) if k]
        if subkeys and all(k in essence or _fuzzy_in(k, essence) for k in subkeys):
            kept.append(w)
    while kept and kept[-1].casefold() in _GLUE:   # a connective left dangling by a dropped tail
        kept.pop()                                 # ('The Red Dahlia by' -> 'The Red Dahlia'); leading
    if not any(w.casefold() not in _GLUE for w in kept):   # articles stay (glue only -> no real title)
        return None
    result = " ".join(kept)
    if [_key(w) for w in _SPLIT.split(result)] == [_key(w) for w in _SPLIT.split(committed)]:
        return None
    return result
