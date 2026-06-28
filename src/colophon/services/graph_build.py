"""Build the entity graph from a scan, and project it back to BookUnits (Phase 1).

`build_graph` runs the existing `plan_scan` (no writes) and wraps each resulting
BookUnit in the node structure; `project` reconstructs source_folder/source_files
from the structural nodes and returns the books. Round-trips to `plan_scan` output.
"""

from __future__ import annotations

from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.models import BookUnit
from colophon.services.ingest import plan_scan


def build_graph(
    repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = ""
) -> Graph:
    """Run a (non-persisting) scan and wrap each BookUnit in Directory/File/Book nodes."""
    plan = plan_scan(repo, root, template=template, directory_scheme=directory_scheme)
    g = Graph()
    for book in plan.units:
        d = g.directories.setdefault(
            DirectoryNode.id_for(book.source_folder),
            DirectoryNode(path=book.source_folder),
        )
        owned: list[str] = []
        for sf in book.source_files:
            fn = FileNode(path=sf.path, role=FileRole.AUDIO, source_file=sf, raw_stem=sf.path.stem)
            g.files[fn.id] = fn
            owned.append(fn.id)
            if fn.id not in d.child_files:
                d.child_files.append(fn.id)
        bn = BookNode(id=book.id, book=book, owns=owned, dir_id=d.id)
        g.books[bn.id] = bn
        if bn.id not in d.books:
            d.books.append(bn.id)
    return g


def project(graph: Graph) -> list[BookUnit]:
    """Reconstruct each Book node's BookUnit, taking source_folder from its
    DirectoryNode and source_files from its owned FileNodes (the structural layer is
    the source for those). Phase 1: the other fields ride on the embedded BookUnit."""
    out: list[BookUnit] = []
    for bn in graph.books.values():
        book = bn.book
        book.source_folder = graph.directories[bn.dir_id].path
        book.source_files = [
            graph.files[fid].source_file
            for fid in bn.owns
            if graph.files[fid].source_file is not None
        ]
        out.append(book)
    return out
