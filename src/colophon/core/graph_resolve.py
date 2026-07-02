"""Confirmed/inferred author-series resolution shared by the scan and match paths.

The scan path classifies AUTHOR directories from descendant evidence and inherits the
author into a subtree's empty-or-weak-author books (GRAPHING; `resolve_graph_authors`),
then propagates confirmed manual classifications onto books (`propagate_overrides`) — a
pure pass over a built Graph. The match path reuses the same fill precedence graph-free,
reading the override store directly (`apply_confirmed_overrides`). Depth-independent; see
the Phase 3b design."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.graph_classify import _series_label
from colophon.core.models import BookUnit, NodeOverride, Provenance, SeriesRef
from colophon.core.normalize import normalize_name

_WEAK = {Provenance.DIRECTORY.value, Provenance.FILENAME.value}

# Provenances that are NOT independent authorship evidence for AUTHOR classification:
# directory/filename are read off the path itself (circular against a folder-name match),
# and graphing is an author we already inferred (using it would feed the inference back
# into itself). Everything else — tag, datafile, manual, and any match source — counts.
_NOT_EVIDENCE = _WEAK | {Provenance.GRAPHING.value}


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


def resolve_graph_authors(graph: Graph, books: list[BookUnit], *, root: Path) -> None:
    """Up: a dir whose name matches a TAG/DATAFILE author of a descendant book is AUTHOR.
    Down: fill each empty-or-weak-author book from its nearest AUTHOR ancestor (GRAPHING)."""
    # Up — classify AUTHOR nodes from independent descendant evidence.
    for book in books:
        if not book.authors or book.provenance.get("authors") in _NOT_EVIDENCE:
            continue
        keys = {_name_key(a): a for a in book.authors}
        for node in _ancestors(graph, book.source_folder, root):
            matched = keys.get(_name_key(node.path.name))
            # author refines a grouping (or a not-yet-classified node); never a
            # classified container/title — those are not author folders.
            if matched is not None and node.kind in ("grouping", "unknown"):
                node.kind = "author"
                node.author = matched

    # Up — structural author: a folder of loose untagged books (a `container`) is its own
    # author, taken from its name. A true multibook folder cannot be a *title* folder (distinct
    # books can't share one title), so it only aligns to author/series/franchise. Franchise is
    # undeterminable and series is eliminated unless EVERY book shares the one series the folder
    # is named for — leaving author as the sole remaining candidate. So reclassify a container to
    # author when (a) it is not the scan root, (b) it has empty/weak-author books to fill, and
    # (c) it is not that single-series-named-after-itself case.
    # Index books by every folder that contains them (the folder itself and each ancestor up
    # to root) in one pass, so the per-container lookup below is O(1) instead of a full rescan
    # of `books` per directory (which is O(dirs x books), ~21s on a 5k-book library).
    books_under: dict[Path, list[BookUnit]] = defaultdict(list)
    for b in books:
        folder = b.source_folder
        books_under[folder].append(b)
        for parent in folder.parents:
            books_under[parent].append(b)
            if parent == root:
                break

    for node in graph.directories.values():
        if node.path == root or node.kind != "container":
            continue
        under = books_under.get(node.path, [])
        if not any(not b.authors or b.provenance.get("authors") in _WEAK for b in under):
            continue  # nothing to fill -> don't claim the folder as an author
        labels = [_series_label(b) for b in under]
        all_one_series = bool(labels) and all(labels) and len({lab[0] for lab in labels if lab}) == 1
        if all_one_series:
            _, series_name, _ = next(lab for lab in labels if lab)
            if _resembles(node.path.name, series_name):
                continue  # every book is the one series this folder is named for -> series tier
        node.kind = "author"
        node.author = node.path.name

    # Down — inherit into empty/weak-author books from the nearest AUTHOR ancestor.
    for book in books:
        prov = book.provenance.get("authors")
        if book.authors and prov not in _WEAK:
            continue  # keep TAG/DATAFILE/GRAPHING/MANUAL authors untouched
        for node in _ancestors(graph, book.source_folder, root):
            if node.kind == "author" and node.author:
                if book.authors != [node.author]:
                    book.authors = [node.author]
                    book.provenance["authors"] = Provenance.GRAPHING.value
                break


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
