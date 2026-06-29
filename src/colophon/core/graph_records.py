"""Map the in-memory structural Graph to relational property-graph records for
persistence (slice 1: structural only). Pure — no I/O. Book nodes are sourced from the
adopted scan units (their #166 re-associated ids are authoritative), while directory
and file nodes + their containment come from the built graph.

A book is its own node, distinct from the directory that holds it — but a single-book
folder's `BookUnit.id` IS that folder's path-hash (`id_for(folder) == DirectoryNode
.id_for(folder)`), so a bare book id would collide with its directory on the nodes PK.
Book node ids are therefore namespaced (`book_node_id`); the bare `BookUnit.id` is kept
in `attrs["book_id"]` for joining back to the book store."""

from __future__ import annotations

from pathlib import Path

from colophon.core.graph import DirectoryNode, FileNode, Graph
from colophon.core.models import BookUnit, _Base


def book_node_id(book_id: str) -> str:
    """The graph node id for a book — namespaced so it never collides with the
    path-hash id of the directory that holds a single-book folder."""
    return f"book:{book_id}"


class NodeRecord(_Base):
    id: str
    physical: str | None       # 'directory' | 'file' | None (logical-only, e.g. a book)
    semantic: str | None       # 'book' | None (author/series/franchise arrive in slice 2)
    root: str
    attrs: dict[str, object] = {}  # noqa: RUF012 - pydantic field default, copied per instance


class EdgeRecord(_Base):
    src: str
    kind: str                  # 'contains' | 'owns'
    dst: str
    root: str
    props: dict[str, object] = {}  # noqa: RUF012 - {} structural; provenance/sequence on semantic edges


def graph_records(
    graph: Graph, units: list[BookUnit], *, root: Path
) -> tuple[list[NodeRecord], list[EdgeRecord]]:
    """Structural records: directory/file nodes + `contains` edges from the graph;
    book nodes + `dir contains book` and `book owns file` edges from `units` (whose ids
    are the persisted, possibly re-associated ones). All stamped with `root`."""
    r = str(root)
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []

    for d in graph.directories.values():
        nodes.append(NodeRecord(
            id=d.id, physical="directory", semantic=None, root=r,
            attrs={"path": str(d.path), "name": d.path.name},
        ))
        for cid in d.child_dirs:
            edges.append(EdgeRecord(src=d.id, kind="contains", dst=cid, root=r))
        for fid in d.child_files:
            edges.append(EdgeRecord(src=d.id, kind="contains", dst=fid, root=r))

    for f in graph.files.values():
        nodes.append(NodeRecord(
            id=f.id, physical="file", semantic=None, root=r,
            attrs={"path": str(f.path), "name": f.path.name, "ext": f.path.suffix, "role": f.role.value},
        ))

    for u in units:
        nid = book_node_id(u.id)
        nodes.append(NodeRecord(
            id=nid, physical=None, semantic="book", root=r,
            attrs={"book_id": u.id, "source_folder": str(u.source_folder)},
        ))
        edges.append(EdgeRecord(
            src=DirectoryNode.id_for(u.source_folder), kind="contains", dst=nid, root=r,
        ))
        for sf in u.source_files:
            edges.append(EdgeRecord(src=nid, kind="owns", dst=FileNode.id_for(sf.path), root=r))

    return nodes, edges
