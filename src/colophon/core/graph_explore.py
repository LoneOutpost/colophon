"""Pure read-only exploration over a LibraryGraph: name search, 1-hop neighborhood, and an
ECharts `graph`-series projection. UI- and store-agnostic (name/confidence are injected), so
the graph explorer's logic is unit-testable without NiceGUI or the book store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph

# Display kinds in a fixed order -> ECharts category index (and color).
_KINDS = ("author", "series", "franchise", "book", "folder", "file")
_KIND_INDEX = {k: i for i, k in enumerate(_KINDS)}
_KIND_COLORS = {
    "author": "#bf5a3c", "series": "#cc8a3c", "franchise": "#8a5a3c",
    "book": "#a89a86", "folder": "#6b7280", "file": "#9aa0a6",
}
_SEMANTIC = ("author", "series", "franchise", "book")


def display_kind(node: NodeRecord) -> str:
    """Color/category bucket: the semantic kind if set, else the physical kind mapped
    (directory -> 'folder', file -> 'file')."""
    if node.semantic in _SEMANTIC:
        return node.semantic
    return "file" if node.physical == "file" else "folder"


@dataclass(frozen=True)
class Subgraph:
    node_ids: list[str]      # focal first, then neighbors (deterministic order)
    edges: list[EdgeRecord]  # edges with both endpoints in node_ids
    omitted: int             # distinct neighbors dropped by the budget cap


def _adjacency(graph: LibraryGraph) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    for e in graph.edges:
        adj.setdefault(e.src, []).append(e.dst)
        adj.setdefault(e.dst, []).append(e.src)
    return adj


def neighborhood(graph: LibraryGraph, focal_id: str, *, hops: int = 1, budget: int = 60) -> Subgraph:
    """BFS `hops` out from `focal_id` over edges (both directions), capped at `budget` nodes.
    `omitted` counts distinct neighbors that would have been included but were dropped by the cap."""
    if focal_id not in graph.nodes:
        return Subgraph(node_ids=[], edges=[], omitted=0)
    adj = _adjacency(graph)
    collected = [focal_id]
    seen = {focal_id}
    dropped: set[str] = set()
    frontier = [focal_id]
    for _ in range(hops):
        nxt: list[str] = []
        for nid in frontier:
            for neigh in sorted(adj.get(nid, [])):      # sorted -> deterministic
                if neigh in seen or neigh in dropped:
                    continue
                if len(collected) >= budget:
                    dropped.add(neigh)
                    continue
                seen.add(neigh)
                collected.append(neigh)
                nxt.append(neigh)
        frontier = nxt
    node_set = set(collected)
    edges = [e for e in graph.edges if e.src in node_set and e.dst in node_set]
    return Subgraph(node_ids=collected, edges=edges, omitted=len(dropped))


def search_nodes(
    graph: LibraryGraph, query: str, *, name_of: Callable[[NodeRecord], str], limit: int = 20
) -> list[str]:
    """Node ids whose resolved name contains `query` (case-insensitive). Semantic nodes
    (author/series/franchise/book) rank ahead of bare directories/files; then by name, then id."""
    q = query.strip().casefold()
    if not q:
        return []
    hits: list[tuple[int, str, str]] = []
    for node in graph.nodes.values():
        name = name_of(node)
        if q in name.casefold():
            rank = 0 if node.semantic in _SEMANTIC else 1
            hits.append((rank, name.casefold(), node.id))
    hits.sort()
    return [nid for _, __, nid in hits[:limit]]


def to_echart(
    graph: LibraryGraph, sub: Subgraph, focal_id: str, *,
    label_of: Callable[[NodeRecord], str],
    confidence_of: Callable[[NodeRecord], float | None],
) -> dict:
    """Project a Subgraph to an ECharts `graph`-series options dict (categories per kind, force
    layout, roam). The focal node is enlarged; series/author/franchise edges are dashed."""
    data = []
    for nid in sub.node_ids:
        node = graph.nodes[nid]
        kind = display_kind(node)
        base = 20 if kind in ("author", "series", "franchise") else 14
        data.append({
            "id": nid,
            "name": label_of(node),
            "category": _KIND_INDEX[kind],
            "symbolSize": 34 if nid == focal_id else base,
            "value": confidence_of(node),
            "itemStyle": {"borderColor": "#ffffff", "borderWidth": 2} if nid == focal_id else {},
        })
    links = [{
        "source": e.src, "target": e.dst,
        "lineStyle": {"type": "dashed"} if e.kind in ("author", "series", "franchise") else {"type": "solid"},
    } for e in sub.edges]
    categories = [{"name": k, "itemStyle": {"color": _KIND_COLORS[k]}} for k in _KINDS]
    return {
        "tooltip": {},
        "legend": [{"data": list(_KINDS)}],
        "series": [{
            "type": "graph", "layout": "force", "roam": True, "draggable": True,
            "label": {"show": True, "position": "right", "color": "#e6ddd2"},
            "force": {"repulsion": 130, "edgeLength": 80, "gravity": 0.05},
            "emphasis": {"focus": "adjacency"},
            "categories": categories, "data": data, "links": links,
        }],
    }
