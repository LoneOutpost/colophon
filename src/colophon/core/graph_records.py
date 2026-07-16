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

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_resolve import _ancestor_paths, _name_key
from colophon.core.models import WEAK_PROV, BookUnit, _Base


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


# Directory kinds that carry a semantic facet onto their node (so the explorer gives them a
# distinct glyph, not the generic folder). 'title' is included so an identified single-book
# title folder reads differently from an unidentified container/grouping folder.
_SEMANTIC_DIR_KINDS = {"author", "series", "franchise", "title"}


def ancestor_franchise(graph: Graph, folder: Path, root: Path) -> str | None:
    """The franchise name of the nearest ancestor directory classified `franchise`, or None.
    This is the folder-derived franchise; a book can also carry its own `franchise` field (a
    manual edit, or this value filled down), resolved by `resolve_book_franchise`."""
    for path in _ancestor_paths(folder, root):
        d = graph.directories.get(DirectoryNode.id_for(path))
        if d is not None and d.kind == "franchise" and d.kind_value:
            return d.kind_value
    return None


# Franchise provenance weak enough to be overwritten by a fresh folder classification
# (a manual assignment is stronger and is preserved). The same weak tier as everywhere else.
_WEAK_FRANCHISE_PROV = WEAK_PROV


def apply_franchise_fill(book: BookUnit, franchise_name: str | None) -> bool:
    """Fill a book's empty-or-weak franchise with `franchise_name`, stamped 'directory'. A
    manual (or otherwise strong) franchise is left untouched. Returns whether the book changed.
    Callers resolve `franchise_name` with the resolver appropriate to their site (a node
    override's verbatim value, or the nearest classified ancestor)."""
    if book.franchise and book.provenance.get("franchise") not in _WEAK_FRANCHISE_PROV:
        return False
    if not franchise_name or book.franchise == franchise_name:
        return False
    book.franchise = franchise_name
    book.provenance["franchise"] = "directory"
    return True


def fill_book_franchise(graph: Graph, book: BookUnit, root: Path) -> bool:
    """Fill a book's empty-or-weak franchise from its nearest ancestor directory classified
    `franchise` (the scan-time resolver). Returns whether the book changed."""
    return apply_franchise_fill(book, ancestor_franchise(graph, book.source_folder, root))


def resolve_book_franchise(book: BookUnit, folder_franchise: str | None) -> str | None:
    """The franchise name for a book's graph edge. A strong (manual) book franchise wins;
    otherwise the folder-derived value takes precedence over a weak (directory/filename) book
    value, so a fresh folder override is not shadowed by a stale folder-fill on the book."""
    if book.franchise and book.provenance.get("franchise") not in _WEAK_FRANCHISE_PROV:
        return book.franchise
    return folder_franchise or book.franchise


class NodeRecord(_Base):
    id: str
    physical: str | None       # 'directory' | 'file' | None (logical-only, e.g. a book)
    semantic: str | None       # 'book' | 'author' | 'series' | 'franchise' | 'title' | None
    root: str
    attrs: dict[str, object] = {}  # noqa: RUF012 - pydantic field default, copied per instance


class EdgeRecord(_Base):
    src: str
    kind: str                  # 'contains' | 'owns' | 'author' | 'series' | 'franchise'
    dst: str
    root: str
    props: dict[str, object] = {}  # noqa: RUF012 - {} structural; provenance/sequence on semantic edges


def skeleton_records(
    graph: Graph, *, root: Path
) -> tuple[list[NodeRecord], list[EdgeRecord]]:
    """The filesystem skeleton: directory nodes (semantic facet from their classification)
    + dir->dir / dir->file `contains` edges + file nodes. Needs the scan Graph; produced at
    scan time. (The half of the graph an edit never changes.)"""
    r = str(root)
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    for d in graph.directories.values():
        attrs: dict[str, object] = {"path": str(d.path), "name": d.path.name, "kind": d.kind}
        if d.kind_value:
            attrs["kind_value"] = d.kind_value
        if d.kind_source:
            attrs["kind_source"] = d.kind_source
        if d.kind_confidence:
            attrs["kind_confidence"] = d.kind_confidence
        if d.kind_evidence:
            attrs["kind_evidence"] = list(d.kind_evidence)
        nodes.append(NodeRecord(
            id=d.id, physical="directory",
            semantic=d.kind if d.kind in _SEMANTIC_DIR_KINDS else None,
            root=r, attrs=attrs,
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
    return nodes, edges


def book_records(
    units: list[BookUnit], *, root: Path, franchise_of: dict[str, str] | None = None
) -> tuple[list[NodeRecord], list[EdgeRecord]]:
    """Book + entity records from the units alone (no scan Graph): book nodes,
    `dir contains book`, `book owns file`, entity nodes, and author/series/franchise edges.
    `franchise_of` maps `book.id` to a franchise name (the only thing that used to need the
    graph). Raw names, matching scan-time output. Re-derivable any time for write-through."""
    r = str(root)
    franchise_of = franchise_of or {}
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
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
        fname = franchise_of.get(u.id)
        if fname:
            _semantic_edge(nid, "franchise", _entity("franchise", fname), {"provenance": "manual"})

    nodes.extend(entities.values())
    return nodes, edges


def _restore_classification(d: DirectoryNode, attrs: dict[str, object]) -> None:
    """Copy the persisted classification (kind + value + confidence + source + evidence) from a
    directory record's attrs back onto its DirectoryNode — the read path, where the tree renders the
    stored classification instead of re-deriving it."""
    d.kind = str(attrs.get("kind", "unknown"))
    kv = attrs.get("kind_value")
    d.kind_value = kv if isinstance(kv, str) else None
    d.author = d.kind_value if d.kind == "author" else None
    d.kind_source = str(attrs.get("kind_source", ""))
    conf = attrs.get("kind_confidence")
    d.kind_confidence = float(conf) if isinstance(conf, int | float) else 0.0
    ev = attrs.get("kind_evidence")
    d.kind_evidence = [str(x) for x in ev] if isinstance(ev, list) else []


def graph_from_records(
    nodes: list[NodeRecord], edges: list[EdgeRecord],
    books_by_id: dict[str, BookUnit], *, root: Path, restore_classification: bool = False,
) -> Graph:
    """Rebuild the structural Graph (directories/files/books + containment) from persisted records,
    WITHOUT a filesystem walk — the input `classify_graph`/`classify_nodes` consume. The inverse of
    `graph_records`'s structural half. `FileNode.source_file` is not restored (classification never
    reads it); a book node whose `book_id` has no BookUnit is skipped (defensive).

    `restore_classification=False` (the re-derive path) leaves each directory's classification empty
    for a fresh classify pass; `True` (the read path) restores the persisted kind/value/confidence/
    source/evidence so the tree can render without reclassifying."""
    r = str(root)
    g = Graph()
    for n in nodes:
        if n.root != r:
            continue
        if n.physical == "directory":
            d = DirectoryNode(path=Path(str(n.attrs["path"])))
            if restore_classification:
                _restore_classification(d, n.attrs)
            g.directories[n.id] = d
        elif n.physical == "file":
            g.files[n.id] = FileNode(
                path=Path(str(n.attrs["path"])),
                role=FileRole(str(n.attrs.get("role", FileRole.AUDIO.value))),
            )
        elif n.semantic == "book":
            bid = n.attrs.get("book_id")
            book = books_by_id.get(bid) if isinstance(bid, str) else None
            if book is not None:
                g.books[n.id] = BookNode(id=n.id, book=book, dir_id="")
    for e in edges:
        if e.root != r:
            continue
        if e.kind == "contains":
            d = g.directories.get(e.src)
            if d is None:
                continue
            if e.dst in g.directories:
                d.child_dirs.append(e.dst)
            elif e.dst in g.files:
                d.child_files.append(e.dst)
            elif e.dst in g.books:
                d.books.append(e.dst)
                g.books[e.dst].dir_id = e.src
        elif e.kind == "owns":
            bn = g.books.get(e.src)
            if bn is not None and e.dst in g.files:
                bn.owns.append(e.dst)
    return g


def prune_dangling_edges(
    nodes: list[NodeRecord], edges: list[EdgeRecord]
) -> list[EdgeRecord]:
    """Keep only edges whose endpoints are both present in `nodes`. `book_records` emits a book's
    `contains`/`owns` edges from the book's own `source_folder`/`source_files`, regardless of
    whether the skeleton has matching directory/file nodes — so after a match/organize moved a
    book's paths, or a rescan dropped a file, the re-emitted edge would reference a node that
    isn't being stored. Dropping those at assembly keeps the persisted graph internally
    consistent (a scan that re-captures the file reconnects the edge)."""
    ids = {n.id for n in nodes}
    return [e for e in edges if e.src in ids and e.dst in ids]


def graph_records(
    graph: Graph, units: list[BookUnit], *, root: Path
) -> tuple[list[NodeRecord], list[EdgeRecord]]:
    """Full scan-time records = filesystem skeleton + book/entity records. Franchise comes
    from the scan graph's ancestor classification (manual overrides)."""
    franchise_of: dict[str, str] = {}
    for u in units:
        fname = resolve_book_franchise(u, ancestor_franchise(graph, u.source_folder, root))
        if fname:
            franchise_of[u.id] = fname
    sk_nodes, sk_edges = skeleton_records(graph, root=root)
    bk_nodes, bk_edges = book_records(units, root=root, franchise_of=franchise_of)
    nodes = sk_nodes + bk_nodes
    return nodes, prune_dangling_edges(nodes, sk_edges + bk_edges)
