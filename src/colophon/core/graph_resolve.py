"""Confirmed manual author-series propagation shared by the scan and match paths.

The evidence engine (node_classify) now classifies AUTHOR directories and inherits inferred
authors into books. This module propagates confirmed *manual* classifications onto books —
on the scan path over a built Graph (`propagate_overrides`), and on the match path graph-free
by reading the override store directly (`apply_confirmed_overrides`). It also hosts the shared
name/series comparison helpers (`_name_key`, `_resembles`) the engine reuses. Depth-independent;
see the Phase 3b design."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, NodeOverride, Provenance, SeriesRef
from colophon.core.normalize import normalize_name

_WEAK = {Provenance.DIRECTORY.value, Provenance.FILENAME.value}


def _name_key(name: str) -> str:
    """Comparison key tolerant of 'Last, First' vs 'First Last', case, spacing, and
    punctuation (periods in initials, etc.). A consistent transform applied to both the
    directory name and the author, so it never invents a match."""
    s = name.strip()
    if "," in s:
        last, _, first = s.partition(",")
        s = f"{first.strip()} {last.strip()}"
    s = normalize_name(s)
    s = re.sub(r"[^\w\s]", " ", s)        # drop punctuation: 'Robert A.' -> 'Robert A'
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def _series_tokens(name: str) -> frozenset[str]:
    """Normalized token set for series-vs-folder comparison: casefold, drop punctuation,
    collapse whitespace. Mirrors `_name_key`'s normalization spirit."""
    s = normalize_name(name)
    s = re.sub(r"[^\w\s]", " ", s)
    return frozenset(re.sub(r"\s+", " ", s).strip().casefold().split())


def _resembles(name: str, other: str) -> bool:
    """True when `name` looks like `other` by normalized tokens: token-set equality, or one
    name's tokens are a subset of the other's (handles 'The Liz Carlyle Novels' ~ 'Liz
    Carlyle'). Used to spot a folder named after a series or a book title (so it is a content
    tier, not an author). Empty on either side -> False."""
    a = _series_tokens(name)
    b = _series_tokens(other)
    if not a or not b:
        return False
    return a == b or a <= b or b <= a


def _ancestor_paths(folder: Path, root: Path) -> Iterator[Path]:
    """Paths from `folder` up to and including `root`, nearest first. Yields `folder`
    itself, then stops once it leaves `root` (so a folder outside `root` yields only
    itself)."""
    cur: Path | None = folder
    while cur is not None:
        yield cur
        if cur == root:
            return
        cur = cur.parent if root in cur.parents else None


def _ancestors(graph: Graph, folder: Path, root: Path) -> list[DirectoryNode]:
    """The DirectoryNodes from `folder` up to (and including) root, nearest first."""
    out: list[DirectoryNode] = []
    for path in _ancestor_paths(folder, root):
        node = graph.directories.get(DirectoryNode.id_for(path))
        if node is not None:
            out.append(node)
    return out


def _fill_confirmed(book: BookUnit, *, author: str | None, series: str | None) -> bool:
    """Fill a book's empty-or-weak (directory/filename) author/series from a confirmed
    (manual) classification, stamped MANUAL. A book that asserts its own author/series
    (tag/datafile/match) is left untouched. Returns whether the book was changed. Shared
    by propagate_overrides (scan path) and apply_confirmed_overrides (match path)."""
    changed = False
    if author and (not book.authors or book.provenance.get("authors") in _WEAK):
        book.authors = [author]
        book.provenance["authors"] = Provenance.MANUAL.value
        changed = True
    if series and (not book.series or book.provenance.get("series") in _WEAK):
        book.series = [SeriesRef(name=series)]
        book.provenance["series"] = Provenance.MANUAL.value
        changed = True
    return changed


def propagate_overrides(graph: Graph, books: list[BookUnit], *, root: Path) -> None:
    """Fill empty/weak author/series on each book from its nearest MANUAL author/series
    ancestor node (classify_nodes stamps kind_source='manual' from an override), stamped
    MANUAL. Authoritative + sticky; a book that asserts its own author/series
    (tag/match/datafile) is left untouched."""
    for book in books:
        author = series = None
        for node in _ancestors(graph, book.source_folder, root):
            if node.kind_source != "manual":
                continue
            if author is None and node.kind == "author" and node.kind_value:
                author = node.kind_value
            if series is None and node.kind == "series" and node.kind_value:
                series = node.kind_value
        _fill_confirmed(book, author=author, series=series)


def franchise_for(source_folder: Path, overrides: dict[str, NodeOverride], *, root: Path) -> str | None:
    """The franchise name of the nearest ancestor directory with a manual `franchise`
    override, or None. The cheap path-walk the navigator uses to derive a book's
    franchise live (no graph build, no store read)."""
    for path in _ancestor_paths(source_folder, root):
        ov = overrides.get(str(path))
        if ov is not None and ov.kind == "franchise" and ov.value:
            return ov.value
    return None


def apply_confirmed_overrides(
    books: list[BookUnit],
    overrides: dict[str, NodeOverride],
    *,
    root_for: Callable[[BookUnit], Path],
) -> list[BookUnit]:
    """Return `books` with each book's empty/weak author/series filled from its nearest
    confirmed (manual) ancestor classification in `overrides` (keyed by folder path),
    stamped MANUAL. The graph-free, match-time analog of propagate_overrides; scope is
    confirmed (manual) classifications only (franchise/container have no book-field
    target). `root_for` gives each book's scan root for the ancestry walk.

    Inputs are never mutated: a book that receives a fill is returned as a deep copy,
    an unaffected book is returned as-is. This keeps the caller's store-cache objects
    clean while letting a persisting caller save the returned copy."""
    out: list[BookUnit] = []
    for book in books:
        author = series = None
        for path in _ancestor_paths(book.source_folder, root_for(book)):
            ov = overrides.get(str(path))
            if ov is None:
                continue
            if author is None and ov.kind == "author" and ov.value:
                author = ov.value
            if series is None and ov.kind == "series" and ov.value:
                series = ov.value
        if not author and not series:
            out.append(book)
            continue
        candidate = book.model_copy(deep=True)
        out.append(candidate if _fill_confirmed(candidate, author=author, series=series) else book)
    return out
