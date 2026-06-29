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

import hashlib
from pathlib import Path

from colophon.core.graph import DirectoryNode, FileNode, Graph
from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookUnit, _Base


def book_node_id(book_id: str) -> str:
    """The graph node id for a book — namespaced so it never collides with the
    path-hash id of the directory that holds a single-book folder."""
    return f"book:{book_id}"


def entity_node_id(kind: str, name: str, root: Path) -> str:
    """A root-scoped, name-deduped id for an author/series/franchise entity node.
    Namespaced by kind so it never collides with a directory/file/book id; two spellings
    of one name (`_name_key`) resolve to the same id."""
    key = f"{root}\x00{kind}\x00{_name_key(name)}"
    return f"{kind}:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


_SEMANTIC_DIR_KINDS = {"author", "series", "franchise"}


class NodeRecord(_Base):
    id: str
    physical: str | None       # 'directory' | 'file' | None (logical-only, e.g. a book)
    semantic: str | None       # 'book' | 'author' | 'series' | 'franchise' | None
    root: str
    attrs: dict[str, object] = {}  # noqa: RUF012 - pydantic field default, copied per instance


class EdgeRecord(_Base):
    src: str
    kind: str                  # 'contains' | 'owns' | 'author' | 'series'
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
            id=d.id, physical="directory",
            semantic=d.kind if d.kind in _SEMANTIC_DIR_KINDS else None,
            root=r, attrs={"path": str(d.path), "name": d.path.name},
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

    entities: dict[str, NodeRecord] = {}
    seen_edges: set[tuple[str, str, str]] = set()

    def _entity(kind: str, name: str) -> str:
        eid = entity_node_id(kind, name, root)
        if eid not in entities:
            entities[eid] = NodeRecord(
                id=eid, physical=None, semantic=kind, root=r,
                attrs={"name": name, "name_key": _name_key(name)},
            )
        return eid

    def _semantic_edge(src: str, kind: str, dst: str, props: dict[str, object]) -> None:
        key = (src, kind, dst)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append(EdgeRecord(src=src, kind=kind, dst=dst, root=r, props=props))

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
        for author in u.authors:
            _semantic_edge(nid, "author", _entity("author", author),
                           {"provenance": u.provenance.get("authors", "")})
        for s in u.series:
            props: dict[str, object] = {"provenance": u.provenance.get("series", "")}
            if s.sequence is not None:
                props["sequence"] = s.sequence
            _semantic_edge(nid, "series", _entity("series", s.name), props)

    nodes.extend(entities.values())
    return nodes, edges
