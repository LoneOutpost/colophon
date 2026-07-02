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
    series_dirs: int = 0
    container_dirs: int = 0
    title_dirs: int = 0
    unknown_dirs: int = 0
    manual_dirs: int = 0
    auto_author: int = 0
    auto_series: int = 0
    books: int = 0
    multi_book_dirs: int = 0
    files_by_role: dict[str, int] = field(default_factory=dict)


def _dir_badges(node: DirectoryNode) -> list[str]:
    """One badge per node: a hard result shows its source (manual/matched), an auto result shows
    its confidence, so an unconfirmed classification reads differently from a settled one."""
    if not node.kind:
        return []
    base = node.kind.upper()
    if node.kind_value:
        base = f"{base} → {node.kind_value}"
    if node.kind_source in ("manual", "matched"):
        return [f"{base} · {node.kind_source}"]
    return [f"{base} · {node.kind_confidence:.2f}"]


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
        tooltip="; ".join(d.kind_evidence),
        path=d.path,
    )


def graph_tree(graph: Graph, root: Path) -> list[GraphTreeNode]:
    """The root's children as a nested tree (dirs, then book leaves, then loose files);
    [] when the root has no DirectoryNode (no books were built under it)."""
    root_id = DirectoryNode.id_for(root)
    if root_id not in graph.directories:
        return []
    return _dir_node(graph, root_id).children


def grouping_cohort(graph: Graph, *, root: Path, hint: str) -> list[DirectoryNode]:
    """Auto-classified (unconfirmed, source == '') nodes of the given kind (author/series),
    excluding the root — the set a 'Confirm all' bulk action promotes to manual. Root is
    excluded: it is the scan path, not a content folder, and confirming it would re-create the
    uploader-name-as-author poison."""
    return [
        d for d in graph.directories.values()
        if d.path != root and d.kind == hint and d.kind_source == ""
    ]


def graph_summary(graph: Graph) -> GraphSummary:
    """Diagnostic counts over the whole built graph."""
    by_role: dict[str, int] = {}
    for fn in graph.files.values():
        by_role[fn.role.value] = by_role.get(fn.role.value, 0) + 1
    return GraphSummary(
        directories=len(graph.directories),
        author_dirs=sum(1 for d in graph.directories.values() if d.kind == "author"),
        series_dirs=sum(1 for d in graph.directories.values() if d.kind == "series"),
        container_dirs=sum(1 for d in graph.directories.values() if d.kind == "container"),
        title_dirs=sum(1 for d in graph.directories.values() if d.kind == "title"),
        unknown_dirs=sum(1 for d in graph.directories.values() if d.kind == "unknown"),
        manual_dirs=sum(1 for d in graph.directories.values() if d.kind_source == "manual"),
        auto_author=sum(
            1 for d in graph.directories.values()
            if d.kind == "author" and d.kind_source == ""),
        auto_series=sum(
            1 for d in graph.directories.values()
            if d.kind == "series" and d.kind_source == ""),
        books=len(graph.books),
        multi_book_dirs=sum(1 for d in graph.directories.values() if len(d.books) > 1),
        files_by_role=by_role,
    )
