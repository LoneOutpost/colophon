"""The grouping engine's Book-bucket election. Axioms cast weighted `GroupBallot`s for "one" (the
files are one book's numbered parts) or "many" (distinct books). `resolve_grouping` seeds a "many"
prior and tallies via the shared `core.ballot.tally`, so "one" wins only when reliable one-evidence
(enumeration / all-structural titles) exceeds the prior and any series-safety vote. See
tasks/2026-08-12-grouping-engine-design.md (Slice 3)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import TYPE_CHECKING

from colophon.core import evidence_weights as W
from colophon.core.ballot import tally
from colophon.core.cohort_constancy import cohort_constant_tokens, cohort_varies_only_by_number
from colophon.core.metadata_quality import is_structural_marker

if TYPE_CHECKING:
    from colophon.core.classify import FileFeatures


@dataclass(frozen=True)
class GroupBallot:
    outcome: str            # "one" | "many"
    weight: float
    reason: str


@dataclass(frozen=True)
class GroupDecision:
    outcome: str            # "one" | "many"
    ballots: list[GroupBallot]


def ax_enumeration(group: list[FileFeatures]) -> list[GroupBallot]:
    """Files differ only by number after removing shared tokens -> one book's numbered parts."""
    if cohort_varies_only_by_number([f.path.stem for f in group]):
        return [GroupBallot("one", W.W_G_ENUMERATION, "files differ only by number (numbered parts)")]
    return []


def ax_index_titles(group: list[FileFeatures]) -> list[GroupBallot]:
    """Every per-file title is a structural marker (chaptering, not distinct books)."""
    titles = [f.tags.title for f in group]
    if titles and all(t for t in titles) and all(is_structural_marker(t) for t in titles):
        return [GroupBallot("one", W.W_G_INDEX_TITLES, "all per-file titles are structural markers")]
    return []


def ax_cohort_constancy(group: list[FileFeatures]) -> list[GroupBallot]:
    """Filename tokens shared across every file are book-level identity (light corroboration)."""
    n = len(cohort_constant_tokens([f.path.stem for f in group]))
    if n:
        w = W.W_G_CONSTANCY_PER_TOKEN * min(n, W.W_G_CONSTANCY_CAP)
        return [GroupBallot("one", w, f"{n} constant filename token(s)")]
    return []


def ax_uniform_work_key(group: list[FileFeatures]) -> list[GroupBallot]:
    """Every file shares one non-structural album/asin/isbn (light corroboration)."""
    from colophon.core.classify import _work_key
    keys = {_work_key(f) for f in group}
    if len(keys) == 1 and None not in keys:
        return [GroupBallot("one", W.W_G_UNIFORM_KEY, f"shared work key '{next(iter(keys))}'")]
    return []


def ax_uniform_author(group: list[FileFeatures]) -> list[GroupBallot]:
    """Every file shares one non-structural author (light corroboration)."""
    from colophon.core.classify import _uniform_tag
    author = _uniform_tag(f.tags.artist for f in group)
    if author and not is_structural_marker(author):
        return [GroupBallot("one", W.W_G_UNIFORM_AUTHOR, f"uniform author '{author}'")]
    return []


def ax_series_of_books(group: list[FileFeatures]) -> list[GroupBallot]:
    """Per-file durations are each whole-book-sized -> these are individual books in a series, not
    parts of one book. The primary multi-book safety axiom."""
    durs = [f.duration_seconds for f in group if f.duration_seconds]
    if durs and median(durs) >= W.W_G_SERIES_MIN_SECONDS:
        return [GroupBallot("many", W.W_G_SERIES,
                            f"per-file median {median(durs) / 3600:.1f}h — whole-book-sized")]
    return []


def ax_uniform_album_chapters(group: list[FileFeatures]) -> list[GroupBallot]:
    """MANY files sharing one non-structural album AND chapter-short per-file durations -> the album
    is the book title and the files are its chapters -> one book. The complement of ax_series_of_books
    (whole-book-long files -> many); catches chapter-named books enumeration can't (distinct chapter
    text, but one uniform album).

    Gated on file count: a genuine shelf-in-a-folder has FEW files (one per distinct book, e.g. a
    box set of 2-3 novels), while a chaptered book has many. Small index-only one-books are already
    caught by ax_enumeration, so this axiom stays out of the few-file shelf's way."""
    from colophon.core.classify import _uniform_tag
    if len(group) < W.W_G_ALBUM_MIN_FILES:
        return []
    album = _uniform_tag(f.tags.album for f in group)
    durs = [f.duration_seconds for f in group if f.duration_seconds]
    if album and not is_structural_marker(album) and durs and median(durs) < W.W_G_SERIES_MIN_SECONDS:
        return [GroupBallot("one", W.W_G_ALBUM_CHAPTERS,
                            f"uniform album '{album}' over {len(group)} chapter-short files")]
    return []


_ONE_AXIOMS = (
    ax_enumeration, ax_index_titles, ax_cohort_constancy, ax_uniform_work_key, ax_uniform_author,
    ax_uniform_album_chapters,
)


def resolve_grouping(group: list[FileFeatures]) -> GroupDecision:
    """Tally a "many" prior + every axiom's ballots. "one" wins only when its summed weight strictly
    exceeds "many"; an empty/tied box defaults to "many"."""
    ballots = [GroupBallot("many", W.W_G_PRIOR,
                           "prior: default to split unless one-evidence exceeds it")]
    for ax in _ONE_AXIOMS:
        ballots.extend(ax(group))
    ballots.extend(ax_series_of_books(group))
    t = tally([(b.outcome, b.weight) for b in ballots], order=("many", "one"))
    return GroupDecision(t.winner or "many", ballots)
