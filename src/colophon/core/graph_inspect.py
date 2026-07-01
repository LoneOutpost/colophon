"""Pure, per-kind read-model over a LibraryGraph: given a focal node, derive the rows, linked
folders, owned files, provenance, and contextual page links to show in the explorer's inspect
panel. UI- and repo-agnostic (names/confidence/provenance are injected), so it is unit-testable
without NiceGUI or the book store. The single reader (`services/graph_inspect`) wires it up."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from colophon.core.graph_explore import display_kind
from colophon.core.graph_records import NodeRecord
from colophon.core.library_graph import LibraryGraph

_ENTITY = ("author", "series", "franchise")


@dataclass(frozen=True)
class NodeLink:
    label: str
    url: str


@dataclass(frozen=True)
class NodeInspection:
    id: str
    label: str
    kind: str                       # display kind, or "" when the focal is missing
    confidence: float | None
    rows: list[tuple[str, str]]     # ordered (label, value) rows, framed per kind
    linked_folders: list[str]       # entity nodes only (0..N folder names); else []
    files: list[str]                # book nodes only
    provenance: list[str]           # human-readable provenance lines
    links: list[NodeLink]           # contextual page links


def _parent_dir(graph: LibraryGraph, node_id: str) -> str | None:
    """The id of the directory that `contains` node_id (its structural parent), or None."""
    for e in graph.edges:
        if e.kind == "contains" and e.dst == node_id and graph.nodes[e.src].physical == "directory":
            return e.src
    return None


def _entity_books(graph: LibraryGraph, entity_id: str, kind: str) -> list[str]:
    """Book node ids joined to `entity_id` by a `kind` edge (book -> entity)."""
    return [e.src for e in graph.edges if e.kind == kind and e.dst == entity_id]


def _linked_folders(graph: LibraryGraph, book_ids: list[str], name_of) -> list[str]:
    """Distinct parent-folder names of the given books, in first-seen order."""
    out: list[str] = []
    for bid in book_ids:
        pid = _parent_dir(graph, bid)
        if pid is None:
            continue
        name = name_of(graph.nodes[pid])
        if name not in out:
            out.append(name)
    return out


def _panel_kind(node: NodeRecord) -> str:
    """The structural kind used for *relationship framing*: a physical directory is always a folder
    (even when it carries a semantic facet, e.g. a classified author folder), a physical file is a
    file, a book is a book, and only a purely-logical author/series/franchise node is framed as an
    entity. Node caption/color/links still use `display_kind`, which is semantic-first."""
    if node.physical == "directory":
        return "folder"
    if node.physical == "file":
        return "file"
    if node.semantic == "book":
        return "book"
    if node.semantic in _ENTITY:
        return node.semantic
    return "folder"


def inspect(
    graph: LibraryGraph, focal_id: str, *,
    name_of: Callable[[NodeRecord], str],
    confidence_of: Callable[[NodeRecord], float | None],
    provenance_of: Callable[[NodeRecord], list[str]],
) -> NodeInspection:
    """The inspect read-model for `focal_id`. Counts are over the full graph, so they are correct
    at any explorer depth. Returns an empty inspection when the focal node is absent."""
    node = graph.nodes.get(focal_id)
    if node is None:
        return NodeInspection(id=focal_id, label="", kind="", confidence=None,
                              rows=[], linked_folders=[], files=[], provenance=[], links=[])
    disp = display_kind(node)          # caption + links + node-color parity (semantic-first)
    pk = _panel_kind(node)             # relationship framing (physical-aware)
    rows: list[tuple[str, str]] = []
    linked_folders: list[str] = []
    files: list[str] = []
    owner: str | None = None

    if pk in _ENTITY:
        books = _entity_books(graph, focal_id, pk)
        linked_folders = _linked_folders(graph, books, name_of)
        label = {"author": "Books by this author", "series": "Books in series",
                 "franchise": "Titles in franchise"}[pk]
        rows.append((label, str(len(books))))
        if pk == "author":
            series = {e.dst for b in books for e in graph.edges if e.src == b and e.kind == "series"}
            rows.append(("Series", str(len(series))))
        provenance = [f"Formed from {len(linked_folders)} classified folder(s)"]
    elif pk == "book":
        pid = _parent_dir(graph, focal_id)
        rows.append(("In folder", name_of(graph.nodes[pid]) if pid else "—"))
        for ek, elabel in (("author", "Author"), ("series", "Series"), ("franchise", "Franchise")):
            names = [name_of(graph.nodes[e.dst]) for e in graph.edges
                     if e.src == focal_id and e.kind == ek]
            if names:
                rows.append((elabel, ", ".join(names)))
        files = [name_of(graph.nodes[e.dst]) for e in graph.edges
                 if e.src == focal_id and e.kind == "owns"]
        rows.append(("Files", str(len(files))))
        provenance = list(provenance_of(node))
    elif pk == "folder":
        pid = _parent_dir(graph, focal_id)
        rows.append(("Parent", name_of(graph.nodes[pid]) if pid else "—"))
        nb = nf = nfi = 0
        for e in graph.edges:
            if e.kind != "contains" or e.src != focal_id:
                continue
            child = graph.nodes[e.dst]
            if child.semantic == "book":
                nb += 1
            elif child.physical == "directory":
                nf += 1
            elif child.physical == "file":
                nfi += 1
        rows.append(("Contains",
                     f"{nb} book{'s' if nb != 1 else ''}, {nf} folder{'s' if nf != 1 else ''}, "
                     f"{nfi} file{'s' if nfi != 1 else ''}"))
        provenance = list(provenance_of(node))
    else:  # file
        pid = _parent_dir(graph, focal_id)
        rows.append(("In folder", name_of(graph.nodes[pid]) if pid else "—"))
        owner = next((e.src for e in graph.edges if e.kind == "owns" and e.dst == focal_id), None)
        rows.append(("Part of book", name_of(graph.nodes[owner]) if owner else "—"))
        ext = node.attrs.get("ext")
        if ext:
            rows.append(("Format", str(ext)))
        provenance = list(provenance_of(node))

    links = _links_for(disp, name_of(node), focal_id)
    if pk == "file" and owner is not None:
        links = [NodeLink("Jump to its book", f"/graph?focal={quote(owner)}")]
    return NodeInspection(
        id=focal_id, label=name_of(node), kind=disp, confidence=confidence_of(node),
        rows=rows, linked_folders=linked_folders, files=files, provenance=provenance,
        links=links,
    )


def _links_for(kind: str, label: str, focal_id: str) -> list[NodeLink]:
    """Contextual page links per kind. Every link resolves to a working view today: Library uses the
    existing `?filter=`, Manage takes `kind`/`filter` params. Franchise has no Manage vocabulary, so
    it gets a Library link only. Folders (classify in 3.2) get none; files get their book-jump from
    `inspect`, which knows the owning book id."""
    q = quote(label)
    manage_label = {"author": "Manage → Authors", "series": "Manage → Series"}
    if kind in ("author", "series"):
        return [
            NodeLink("Open in Library", f"/?filter={q}"),
            NodeLink(manage_label[kind], f"/manage?kind={kind}&filter={q}"),
        ]
    if kind == "franchise":
        return [NodeLink("Open in Library", f"/?filter={q}")]
    if kind == "book":
        return [NodeLink("Open in Library", f"/?filter={q}")]
    return []
