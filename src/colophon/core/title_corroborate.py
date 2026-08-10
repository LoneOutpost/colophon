"""Decide whether a book's metadata title is corroborated by its folder and filenames.

Pure over strings. The title is a *residual*: each source (tag / filename / folder) is reduced by
subtracting every positively-identified element — author tokens, the series name, the franchise —
and any sequence/track/series-code numbering. The verdict:

- agree     — the tag residual shares a word with a real folder/filename residual.
- abstain   — the tag has no residual (placeholder/echo), OR no structural source has one
              (author-only folder + chaptered files). No confidence change.
- contradict— a real structural residual exists but shares NO word with the tag residual.

This slice is confidence-only: `suggested_title` is advisory (finding text + future repair), never
written to the stored title.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from colophon.core.filename_cluster import _spaced, _tokens
from colophon.core.folder_title import parse_folder_title
from colophon.core.match import clean_match_title
from colophon.core.metadata_quality import is_junk_title
from colophon.core.normalize import collides_with_title
from colophon.core.sequence_affix import parse_sequence_affix, strip_series_code_affix

if TYPE_CHECKING:
    from colophon.core.models import BookUnit

Verdict = Literal["agree", "abstain", "contradict"]

# A series-book number stuffed into an author value: "... (Flinx 03)", "... (The Expanse #4)".
_SERIES_PAREN = re.compile(r"\(\s*.+?#?\s*\d", re.IGNORECASE)

# Words that carry no title identity: connectives/articles, and structural/edition markers that
# routinely stand alone as a junk tag title ("Unabridged", "Track 01", "Disk 1"). A residual made of
# only these is not a real title, and two titles must not "agree" merely by sharing one.
_STOPWORDS = frozenset({"of", "the", "a", "an", "and", "or", "to", "in", "on", "at", "by", "for", "with"})
_NON_TITLE_WORDS = frozenset({
    "track", "disc", "disk", "cd", "chapter", "chap", "volume", "vol", "part", "side",
    "unabridged", "abridged", "intro", "outro", "prologue", "epilogue",
    "no", "none", "title", "notitle", "untitled", "unknown",
})




@dataclass(frozen=True)
class TitleCorroboration:
    verdict: Verdict
    agreeing_sources: tuple[str, ...] = ()
    evidence: str = ""
    suggested_title: str | None = None


# Word extraction that splits on ALL punctuation ('.', '-', ':' included, which _tokens/_spaced keep
# glued). Comparison must be punctuation-insensitive so "The Land: Founding" and a filename
# "...The Land Bk1...Founding" share their words. Numbers and single letters carry no title meaning.
_WORD = re.compile(r"[a-z0-9]+")


def _words(value: str) -> set[str]:
    return {w for w in _WORD.findall(value.lower()) if len(w) >= 2 and not w.isdigit()}


def _title_words(value: str) -> set[str]:
    """The meaningful words of a title: drop numbers, single letters, connectives, and structural/
    edition markers. This is the unit of both 'is this a real title' and 'do two titles agree'."""
    return _words(value) - _STOPWORDS - _NON_TITLE_WORDS


def _shares_word(a: str, b: str) -> bool:
    return bool(_title_words(a) & _title_words(b))


def _has_text(value: str) -> bool:
    # A residual is real only if it has a meaningful title word — so bare articles/initials/numbers
    # or a lone marker ("Unabridged", "Track 01") read as empty and the source abstains.
    return bool(_title_words(value))


def _subtract(text: str, *drop_terms: str) -> str:
    """Drop tokens (>=2 chars) that appear in any drop term, returning the spaced remainder."""
    drop: set[str] = set()
    for term in drop_terms:
        drop |= {t for t in _tokens(term) if len(t) >= 2}
    kept = [w for w in _spaced(text).split() if w.lower() not in drop]
    return " ".join(kept).strip()


def _clean_affixes(text: str) -> str:
    text = strip_series_code_affix(text)
    aff = parse_sequence_affix(text)
    if aff is not None and aff.confidence == "strong":
        text = aff.cleaned
    return clean_match_title(text, strip_year=False)


def _tag_residual(tag_title: str | None, noise: list[str], authors: list[str]) -> str:
    if is_junk_title(tag_title):                                                # A1
        return ""
    if len(authors) == 1 and collides_with_title(tag_title, authors[0]):        # A3
        return ""
    residual = _subtract(_clean_affixes(tag_title), *noise)
    return residual if _has_text(residual) else ""


def _structural_residual(name: str, noise: list[str]) -> str:
    if is_junk_title(name):
        return ""
    base = parse_folder_title(name).title or name   # strips year / read-by / series-paren / book-num
    residual = _clean_affixes(_subtract(base, *noise))
    return residual if _has_text(residual) else ""


def _filename_residual(filenames: list[str], noise: list[str]) -> str:
    """The title shared across the files: per-file residuals intersected on words, so chaptered files
    ('Track 001', 'Cujo - Chapter 2') either abstain or contribute only the common title word."""
    residuals = [r for fn in filenames if (r := _structural_residual(fn, noise))]
    if not residuals:
        return ""
    common = _words(residuals[0])
    for r in residuals[1:]:
        common &= _words(r)
    # keep the shared words, first-seen order, so a chaptered set distills to its common title
    kept = " ".join(dict.fromkeys(w for w in _WORD.findall(residuals[0].lower()) if w in common))
    return kept if _has_text(kept) else ""


def corroborate_title(
    tag_title: str | None, filenames: list[str], folder_name: str,
    authors: list[str], series_name: str | None = None, franchise: str | None = None,
) -> TitleCorroboration:
    # Title is the residual: subtract every positively-identified element as potential noise.
    noise = [t for t in (series_name, franchise) if t]
    struct_noise = [*authors, *noise]
    tag = _tag_residual(tag_title, noise, authors)
    present = [
        (src, res) for src, res in (
            ("folder", _structural_residual(folder_name, struct_noise)),
            ("filename", _filename_residual(filenames, struct_noise)),
        ) if res
    ]
    if not tag or not present:
        return TitleCorroboration(verdict="abstain")
    agreeing = tuple(src for src, res in present if _shares_word(tag, res))
    if agreeing:
        return TitleCorroboration(
            verdict="agree", agreeing_sources=agreeing,
            evidence=f'tag "{tag}" ~ {agreeing[0]}', suggested_title=tag_title,
        )
    src0, res0 = present[0]
    return TitleCorroboration(
        verdict="contradict",
        evidence=f'metadata title "{tag}" vs {src0} "{res0}"', suggested_title=res0,
    )


def author_looks_like_title(author: str, title: str | None) -> bool:
    """B1 guard: an author value that is really a title — carries a series-paren number, a strong
    sequence affix, or simply equals the title. Used to demote the author axis, never to edit it."""
    if not author:
        return False
    if _SERIES_PAREN.search(author):
        return True
    if parse_sequence_affix(author) is not None:
        return True
    return bool(title and collides_with_title(author, title))


def book_title_verdict(book: BookUnit) -> TitleCorroboration:
    """Adapter: run `corroborate_title` from a BookUnit's own fields (no graph needed). Passes every
    resolved entity — author, series, franchise — as title noise to subtract (title-as-residual)."""
    series_name = book.series[0].name if book.series else None
    filenames = [sf.path.stem for sf in book.source_files]
    folder = book.source_folder.name if book.source_folder is not None else ""
    return corroborate_title(book.title, filenames, folder, book.authors, series_name, book.franchise)
