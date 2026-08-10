"""Render-agnostic projections of a built Graph for the folder-classification /graph view: a
directory-only tree with per-folder rollups (folder_rows) and summary counts (graph_summary).
Pure; no UI dependency."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import SUPPRESSED_FINDINGS


@dataclass
class FolderRow:
    """One directory in the classification view. Books and files are never rows here; their weight
    is rolled up into book_count / attention_count so the view stays a folder-structure instrument."""

    node_kind: str            # always "dir" (kept for symmetry with the old tree API)
    label: str
    path: Path
    badges: list[str] = field(default_factory=list)
    children: list[FolderRow] = field(default_factory=list)
    tooltip: str = ""
    book_count: int = 0        # books in this folder's subtree (own + descendants)
    attention_count: int = 0   # subtree books carrying an active finding
    needs_review: bool = False  # unknown, or auto-classified and unconfirmed
    multi_book: bool = False    # a folder holding >1 book (a title folder that should be one)
    is_container_shape: bool = False  # loose audio alongside subfolders
    kind: str = ""              # raw classification kind (for filtering); "" = unclassified
    kind_source: str = ""       # "manual" / "matched" / "" (auto), for the manual filter


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


def _book_has_active_finding(book) -> bool:
    """True when the book carries a finding that is neither acknowledged nor globally suppressed —
    the same 'active finding' rule the Attention view uses, rolled up to folders here."""
    return any(
        f.code not in book.acknowledged_findings and f.code not in SUPPRESSED_FINDINGS
        for f in book.findings
    )


def _folder_row(graph: Graph, dir_id: str) -> FolderRow:
    d = graph.directories[dir_id]
    children = sorted(
        (_folder_row(graph, cid) for cid in d.child_dirs if cid in graph.directories),
        key=lambda r: r.label.casefold(),
    )
    own_books = [graph.books[bid].book for bid in d.books if bid in graph.books]
    own_attention = sum(1 for b in own_books if _book_has_active_finding(b))
    return FolderRow(
        node_kind="dir",
        label=d.path.name,
        path=d.path,
        badges=_dir_badges(d),
        children=children,
        tooltip="; ".join(d.kind_evidence),
        book_count=len(own_books) + sum(c.book_count for c in children),
        attention_count=own_attention + sum(c.attention_count for c in children),
        needs_review=(d.kind == "unknown") or (bool(d.kind) and d.kind_source == ""),
        multi_book=len(d.books) > 1,
        is_container_shape=bool(d.child_files and d.child_dirs),
        kind=d.kind,
        kind_source=d.kind_source,
    )


def folder_rows(graph: Graph, root: Path) -> list[FolderRow]:
    """The root's sub-directories as a nested, directory-only tree with per-folder rollups (subtree
    book/attention counts, review + structural flags). [] when the root has no DirectoryNode."""
    root_id = DirectoryNode.id_for(root)
    if root_id not in graph.directories:
        return []
    return _folder_row(graph, root_id).children


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
