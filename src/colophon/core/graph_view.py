"""Render-agnostic projections of a built Graph for the diagnostic /graph view: a nested
tree (graph_tree) and summary counts (graph_summary). Pure; no UI dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph


@dataclass
class GraphTreeNode:
    node_kind: str            # "dir" | "book" | "file"
    label: str
    badges: list[str] = field(default_factory=list)
    children: list[GraphTreeNode] = field(default_factory=list)
    tooltip: str = ""
    path: Path | None = None


@dataclass
class GraphSummary:
    directories: int = 0
    author_dirs: int = 0
    grouping_dirs: int = 0
    container_dirs: int = 0
    title_dirs: int = 0
    unknown_dirs: int = 0
    manual_dirs: int = 0
    grouping_author_hint: int = 0
    grouping_series_hint: int = 0
    grouping_ambiguous_hint: int = 0
    books: int = 0
    multi_book_dirs: int = 0
    files_by_role: dict[str, int] = field(default_factory=dict)


def _dir_badges(node: DirectoryNode) -> list[str]:
    if node.kind_source == "manual":
        base = node.kind.upper()
        if node.kind_value:
            base = f"{base} → {node.kind_value}"
        return [f"{base} · manual"]
    if node.kind == "author":
        return [f"AUTHOR → {node.author}"] if node.author else ["AUTHOR"]
    if node.kind in ("grouping", "container", "title"):
        badges = [f"{node.kind.upper()} · {node.kind_confidence:.2f}"]
        if node.kind == "grouping" and node.kind_hint:
            badges.append(f"{node.kind_hint}? · {node.kind_hint_confidence:.2f}")
        return badges
    return []


def _file_node(graph: Graph, file_id: str) -> GraphTreeNode:
    fn = graph.files[file_id]
    return GraphTreeNode("file", fn.path.name, [fn.role.value])


def _book_node(graph: Graph, book_id: str) -> GraphTreeNode:
    bn = graph.books[book_id]
    book = bn.book
    badges = [book.content_kind.value]
    if book.authors:
        badges.append(f"author: {book.provenance.get('authors', '?')}")
    children = sorted(
        (_file_node(graph, fid) for fid in bn.owns if fid in graph.files),
        key=lambda n: n.label.casefold(),
    )
    return GraphTreeNode("book", book.title or "(untitled)", badges, children)


def _dir_node(graph: Graph, dir_id: str) -> GraphTreeNode:
    d = graph.directories[dir_id]
    owned = {fid for bid in d.books if bid in graph.books for fid in graph.books[bid].owns}
    child_dirs = sorted(
        (_dir_node(graph, cid) for cid in d.child_dirs if cid in graph.directories),
        key=lambda n: n.label.casefold(),
    )
    books = sorted(
        (_book_node(graph, bid) for bid in d.books if bid in graph.books),
        key=lambda n: n.label.casefold(),
    )
    loose = sorted(
        (_file_node(graph, fid) for fid in d.child_files
         if fid in graph.files and fid not in owned),
        key=lambda n: n.label.casefold(),
    )
    return GraphTreeNode(
        "dir", d.path.name, _dir_badges(d), [*child_dirs, *books, *loose],
        tooltip="; ".join([*d.kind_evidence, *d.kind_hint_evidence]),
        path=d.path,
    )


def graph_tree(graph: Graph, root: Path) -> list[GraphTreeNode]:
    """The root's children as a nested tree (dirs, then book leaves, then loose files);
    [] when the root has no DirectoryNode (no books were built under it)."""
    root_id = DirectoryNode.id_for(root)
    if root_id not in graph.directories:
        return []
    return _dir_node(graph, root_id).children


def graph_summary(graph: Graph) -> GraphSummary:
    """Diagnostic counts over the whole built graph."""
    by_role: dict[str, int] = {}
    for fn in graph.files.values():
        by_role[fn.role.value] = by_role.get(fn.role.value, 0) + 1
    return GraphSummary(
        directories=len(graph.directories),
        author_dirs=sum(1 for d in graph.directories.values() if d.kind == "author"),
        grouping_dirs=sum(1 for d in graph.directories.values() if d.kind == "grouping"),
        container_dirs=sum(1 for d in graph.directories.values() if d.kind == "container"),
        title_dirs=sum(1 for d in graph.directories.values() if d.kind == "title"),
        unknown_dirs=sum(1 for d in graph.directories.values() if d.kind == "unknown"),
        manual_dirs=sum(1 for d in graph.directories.values() if d.kind_source == "manual"),
        grouping_author_hint=sum(
            1 for d in graph.directories.values()
            if d.kind == "grouping" and d.kind_hint == "author"),
        grouping_series_hint=sum(
            1 for d in graph.directories.values()
            if d.kind == "grouping" and d.kind_hint == "series"),
        grouping_ambiguous_hint=sum(
            1 for d in graph.directories.values()
            if d.kind == "grouping" and d.kind_hint == "ambiguous"),
        books=len(graph.books),
        multi_book_dirs=sum(1 for d in graph.directories.values() if len(d.books) > 1),
        files_by_role=by_role,
    )
