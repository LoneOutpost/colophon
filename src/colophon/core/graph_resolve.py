"""Classify AUTHOR directories from descendant evidence, then inherit the author into a
subtree's empty-or-weak-author books (GRAPHING). Depth-independent; see the Phase 3b
design. A pure pass over a built Graph and the scan's resolved books."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, Provenance, SeriesRef
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


def _fill_confirmed(book: BookUnit, *, author: str | None, series: str | None) -> None:
    """Fill a book's empty-or-weak (directory/filename) author/series from a confirmed
    (manual) classification, stamped MANUAL. A book that asserts its own author/series
    (tag/datafile/match) is left untouched. Shared by propagate_overrides (scan path)
    and apply_confirmed_overrides (match path)."""
    if author and (not book.authors or book.provenance.get("authors") in _WEAK):
        book.authors = [author]
        book.provenance["authors"] = Provenance.MANUAL.value
    if series and (not book.series or book.provenance.get("series") in _WEAK):
        book.series = [SeriesRef(name=series)]
        book.provenance["series"] = Provenance.MANUAL.value


def propagate_overrides(graph: Graph, books: list[BookUnit], *, root: Path) -> None:
    """Fill empty/weak author/series on each book from its nearest MANUAL author/series
    ancestor node (set by apply_overrides), stamped MANUAL. Authoritative + sticky; a book
    that asserts its own author/series (tag/match/datafile) is left untouched."""
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
