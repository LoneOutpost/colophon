"""The single read path for the graph explorer: search, neighborhood projection, and the per-kind
inspect read-model. Wires the book store and persisted node provenance to the pure `core`
projections (`graph_explore`, `graph_inspect`); nothing else reads the graph for the UI, and no
provenance is duplicated — it is composed on read from its one persisted home."""

from __future__ import annotations

from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.graph_explore import (
    display_kind,
    neighborhood,
    search_nodes,
    to_echart,
    type_label,
)
from colophon.core.graph_inspect import NodeInspection
from colophon.core.graph_inspect import inspect as _inspect
from colophon.core.graph_records import NodeRecord
from colophon.core.library_graph import LibraryGraph


def _name_of(books: BookUnitRepo):
    def name(node: NodeRecord) -> str:
        book_id = node.attrs.get("book_id")
        if book_id:
            book = books.get(str(book_id))
            if book is not None:
                return book.title or Path(str(node.attrs.get("source_folder", ""))).name or node.id
        return str(node.attrs.get("name") or node.id)
    return name


def _confidence_of(books: BookUnitRepo):
    def conf(node: NodeRecord) -> float | None:
        book_id = node.attrs.get("book_id")
        if book_id:
            book = books.get(str(book_id))
            return book.confidence if book is not None else None
        return None
    return conf


def _provenance_of(books: BookUnitRepo):
    """Human-readable provenance lines composed from the node's one persisted home: folder
    classification props (written at scan time) or the book's own `provenance`/`confidence`."""
    def prov(node: NodeRecord) -> list[str]:
        kind = display_kind(node)
        if kind == "book":
            book_id = node.attrs.get("book_id")
            book = books.get(str(book_id)) if book_id else None
            if book is None:
                return []
            lines = ["Identity: matched (ASIN)" if book.asin else "Identity: filename-derived"]
            for field in ("title", "authors", "series"):
                src = book.provenance.get(field)
                if src:
                    lines.append(f"{field.capitalize()} from {src}")
            lines.append(f"Confidence {book.confidence:.2f}")
            return lines
        if node.physical == "directory":
            if "kind" not in node.attrs:
                return ["Re-scan to compute provenance"]
            k = str(node.attrs.get("kind"))
            if k == "unknown":
                return ["Unclassified"]
            manual = node.attrs.get("kind_source") == "manual"
            conf = node.attrs.get("kind_confidence")
            head = f"Classified as {k} · {'confirmed manually' if manual else 'auto rule'}"
            if conf:
                head += f" · confidence {float(conf):.2f}"
            out = [head]
            out.extend(str(x) for x in (node.attrs.get("kind_evidence") or []))
            return out
        return []
    return prov


def search(graph: LibraryGraph, books: BookUnitRepo, query: str) -> list[dict]:
    """Focal candidates for the explorer search box: [{id, label, kind}]."""
    name_of = _name_of(books)
    ids = search_nodes(graph, query, name_of=name_of)
    return [{"id": nid, "label": name_of(graph.nodes[nid]), "kind": type_label(graph.nodes[nid])}
            for nid in ids]


def neighborhood_view(
    graph: LibraryGraph, books: BookUnitRepo, focal_id: str, *,
    depth: int, hidden: frozenset[str],
) -> dict:
    """The ECharts options + omitted count for `focal_id`'s `depth`-hop neighborhood."""
    sub = neighborhood(graph, focal_id, hops=depth)
    echart = to_echart(graph, sub, focal_id, hidden=hidden,
                       label_of=_name_of(books), confidence_of=_confidence_of(books))
    return {"echart": echart, "omitted": sub.omitted}


def inspect(graph: LibraryGraph, books: BookUnitRepo, focal_id: str) -> NodeInspection:
    """The per-kind inspect read-model for the focal node (the panel's whole content). Manual
    decisions need no separate repo here — they're already stamped into the node's persisted
    provenance props at scan time (`kind_source == "manual"`)."""
    return _inspect(graph, focal_id, name_of=_name_of(books),
                    confidence_of=_confidence_of(books), provenance_of=_provenance_of(books))
