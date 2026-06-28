"""Coarse, decision-support classification of DirectoryNodes: grouping / container /
title / unknown, each with a confidence and human-readable evidence. A pure, bottom-up
pass over a built Graph. This is a triage layer — it does NOT make the final author/series
determination; later passes (and the user) refine a grouping into author or series."""

from __future__ import annotations

from collections import Counter  # noqa: F401 - used by the shape prior (next task)
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph

GROUPING = "grouping"
CONTAINER = "container"
TITLE = "title"
UNKNOWN = "unknown"

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


def classify_graph(graph: Graph, *, root: Path) -> None:
    """Assign each DirectoryNode a coarse kind + confidence + evidence. Deepest-first so a
    parent sees its children classified ('book-like' is recursive)."""
    nodes = sorted(graph.directories.values(), key=lambda d: _depth(d, root), reverse=True)
    for node in nodes:
        _classify_node(node, graph)
