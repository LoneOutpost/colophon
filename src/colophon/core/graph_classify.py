"""Coarse, decision-support classification of DirectoryNodes: grouping / container /
title / unknown, each with a confidence and human-readable evidence. A pure, bottom-up
pass over a built Graph. This is a triage layer — it does NOT make the final author/series
determination; later passes (and the user) refine a grouping into author or series."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.normalize import normalize_name

GROUPING = "grouping"
CONTAINER = "container"
TITLE = "title"
UNKNOWN = "unknown"

HINT_AUTHOR = "author"
HINT_SERIES = "series"
HINT_AMBIGUOUS = "ambiguous"
_SERIES_COVERAGE = 0.6      # dominant series must cover this fraction of leaf books

_BOOK_LIKE = {TITLE, GROUPING}


def _depth(node: DirectoryNode, root: Path) -> int:
    try:
        return len(node.path.relative_to(root).parts)
    except ValueError:
        return 0


def _container_signals(node: DirectoryNode) -> list[str]:
    """Positive container evidence — never the mere absence of grouping, so an uploader
    folder full of author folders is not mislabeled."""
    ev: list[str] = []
    if len(node.books) > 1:
        ev.append(f"{len(node.books)} loose books in one folder")
    if node.child_files and node.child_dirs:
        ev.append(f"loose audio alongside {len(node.child_dirs)} subfolders")
    return ev


def _classify_node(node: DirectoryNode, graph: Graph) -> None:
    signals = _container_signals(node)
    if signals:
        node.kind = CONTAINER
        node.kind_confidence = 0.9 if len(node.books) > 1 else 0.7
        node.kind_evidence = signals
        return
    if len(node.books) == 1 and not node.child_dirs:
        node.kind = TITLE
        node.kind_confidence = 1.0
        node.kind_evidence = ["single book leaf"]
        return
    if node.child_dirs:
        children = [graph.directories[c] for c in node.child_dirs if c in graph.directories]
        book_like = [c for c in children if c.kind in _BOOK_LIKE]
        n = len(children)
        if n and len(book_like) / n > 0.5:
            node.kind = GROUPING
            node.kind_confidence = round(len(book_like) / n, 2)
            node.kind_evidence = [f"{len(book_like)} of {n} child folders are book-like"]
            return
    node.kind = UNKNOWN
    node.kind_confidence = 0.0
    node.kind_evidence = ["mixed/insufficient structure"]


def _apply_shape_prior(graph: Graph, *, root: Path) -> None:
    """The tree's consistency is itself evidence: boost groupings that sit at the typical
    author depth (one above the modal title depth), flag the rest for review."""
    title_depths = Counter(
        _depth(d, root) for d in graph.directories.values() if d.kind == TITLE
    )
    if not title_depths:
        return
    modal = title_depths.most_common(1)[0][0]
    for node in graph.directories.values():
        if node.kind != GROUPING:
            continue
        if _depth(node, root) == modal - 1:
            node.kind_confidence = round(min(1.0, node.kind_confidence + 0.1), 2)
            node.kind_evidence.append("matches dominant Author/Title shape")
        else:
            node.kind_evidence.append("off-pattern: not at the typical author depth")


def classify_graph(graph: Graph, *, root: Path) -> None:
    """Assign each DirectoryNode a coarse kind + confidence + evidence. Deepest-first so a
    parent sees its children classified ('book-like' is recursive)."""
    nodes = sorted(graph.directories.values(), key=lambda d: _depth(d, root), reverse=True)
    for node in nodes:
        _classify_node(node, graph)
    _apply_shape_prior(graph, root=root)


def _subtree_books(graph: Graph, node: DirectoryNode) -> list:
    """All BookUnits in `node`'s subtree (its own books + recursively its child dirs')."""
    out = [graph.books[bid].book for bid in node.books if bid in graph.books]
    for cid in node.child_dirs:
        child = graph.directories.get(cid)
        if child is not None:
            out.extend(_subtree_books(graph, child))
    return out


def _series_label(book) -> tuple[str, str, float | None] | None:
    """A leaf book's (normalized-key, display-name, sequence), or None if it has no series.
    Prefers a resolved SeriesRef; falls back to the clusterer's detected series."""
    name: str | None = None
    seq: float | None = None
    if book.series and book.series[0].name:
        name, seq = book.series[0].name, book.series[0].sequence
    elif book.detected_works and book.detected_works[0].series:
        name, seq = book.detected_works[0].series, book.detected_works[0].sequence
    if not name:
        return None
    return normalize_name(name).casefold(), name, seq


def _hint_for(books: list) -> tuple[str, float, list[str]]:
    """Author/series hint for a grouping from its subtree leaf books. One dominant series
    with a sequence ramp -> series; multiple series or standalone titles -> author; one
    series without a ramp -> ambiguous."""
    n = len(books)
    if n == 0:
        return "", 0.0, []
    by_series: dict[str, list[float | None]] = {}
    display: dict[str, str] = {}
    for book in books:
        label = _series_label(book)
        if label is None:
            continue
        key, name, seq = label
        by_series.setdefault(key, []).append(seq)
        display.setdefault(key, name)
    if not by_series:
        return HINT_AUTHOR, 0.6, [f"{n} titles, no series information"]
    dominant = max(by_series, key=lambda k: len(by_series[k]))
    coverage = len(by_series[dominant]) / n
    seqs = sorted({s for s in by_series[dominant] if s is not None})
    if len(by_series) == 1 and coverage >= _SERIES_COVERAGE:
        if len(seqs) >= 2:
            return HINT_SERIES, round(coverage, 2), [
                f"{len(by_series[dominant])} of {n} books in series "
                f"'{display[dominant]}' (seq {seqs[0]:g}–{seqs[-1]:g})"  # noqa: RUF001 - en dash for numeric range
            ]
        return HINT_AMBIGUOUS, 0.3, [
            f"one series '{display[dominant]}' but no clear sequence ramp"
        ]
    return HINT_AUTHOR, round(min(0.9, 0.5 + 0.1 * len(by_series)), 2), [
        f"spans {len(by_series)} series across {n} titles"
    ]


def hint_grouping_kinds(graph: Graph) -> None:
    """Write an advisory author/series hint onto each still-unresolved grouping (kind ==
    'grouping'). Runs after author resolution; never changes `kind`."""
    for node in graph.directories.values():
        if node.kind != GROUPING:
            continue
        hint, conf, evidence = _hint_for(_subtree_books(graph, node))
        node.kind_hint = hint
        node.kind_hint_confidence = conf
        node.kind_hint_evidence = evidence
