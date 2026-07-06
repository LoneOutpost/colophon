"""Pure read-only exploration over a LibraryGraph: name search, 1-hop neighborhood, and an
ECharts `graph`-series projection. UI- and store-agnostic (name/confidence are injected), so
the graph explorer's logic is unit-testable without NiceGUI or the book store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph

# Display kinds in a fixed order -> ECharts category index. Public: the UI legend iterates these.
KINDS = ("author", "series", "franchise", "book", "title", "folder", "file")
_KIND_INDEX = {k: i for i, k in enumerate(KINDS)}

# Dual-mode categorical fills: mid-tone so each reads on both the light paper surface and the dark
# (#1c1916) surface, mutually distinct, and theme-harmonious (terracotta reserved for author).
KIND_COLOR = {
    "author": "#c15a38", "series": "#d09a2c", "franchise": "#4a72a8",
    "book": "#2e8f80", "title": "#9c5a6f", "folder": "#7f8a3c", "file": "#9a8d7c",
}

# Material Symbols (outlined) icon name per kind — used for the legend (ui.icon) and, as SVG path
# data, for the ECharts node symbol below. Shape is the primary, color-independent cue.
KIND_ICON = {
    "author": "person", "series": "layers", "franchise": "collections_bookmark",
    "book": "menu_book", "title": "folder_special", "folder": "folder", "file": "description",
}

# Node marker chrome (all dual-mode: mid-tones / terracotta read on both surfaces).
_NODE_BORDER = "#8a7c64"   # thin warm-neutral border on every node so it lifts off either background
_FOCAL_RING = "#b04e30"    # theme PRIMARY: the "you are here" selection ring
_FOCAL_GLOW = "#d6754f"    # theme ACCENT_DARK: focal shadow glow

# Per-kind ECharts node glyph: the Material Symbols path (24px, 0 -960 960 960 viewBox) as a
# `path://` symbol. ECharts fits each path's bounding box into `symbolSize`.
_KIND_SYMBOL = {
    "author": "path://M480-480q-66 0-113-47t-47-113q0-66 47-113t113-47q66 0 113 47t47 113q0 66-47 113t-113 47ZM160-160v-112q0-34 17.5-62.5T224-378q62-31 126-46.5T480-440q66 0 130 15.5T736-378q29 15 46.5 43.5T800-272v112H160Zm80-80h480v-32q0-11-5.5-20T700-306q-54-27-109-40.5T480-360q-56 0-111 13.5T260-306q-9 5-14.5 14t-5.5 20v32Zm240-320q33 0 56.5-23.5T560-640q0-33-23.5-56.5T480-720q-33 0-56.5 23.5T400-640q0 33 23.5 56.5T480-560Zm0-80Zm0 400Z",
    "series": "path://M480-118 120-398l66-50 294 228 294-228 66 50-360 280Zm0-202L120-600l360-280 360 280-360 280Zm0-280Zm0 178 230-178-230-178-230 178 230 178Z",
    "franchise": "path://M320-320h480v-480h-80v280l-100-60-100 60v-280H320v480Zm0 80q-33 0-56.5-23.5T240-320v-480q0-33 23.5-56.5T320-880h480q33 0 56.5 23.5T880-800v480q0 33-23.5 56.5T800-240H320ZM160-80q-33 0-56.5-23.5T80-160v-560h80v560h560v80H160Zm360-720h200-200Zm-200 0h480-480Z",
    "book": "path://M560-564v-68q33-14 67.5-21t72.5-7q26 0 51 4t49 10v64q-24-9-48.5-13.5T700-600q-38 0-73 9.5T560-564Zm0 220v-68q33-14 67.5-21t72.5-7q26 0 51 4t49 10v64q-24-9-48.5-13.5T700-380q-38 0-73 9t-67 27Zm0-110v-68q33-14 67.5-21t72.5-7q26 0 51 4t49 10v64q-24-9-48.5-13.5T700-490q-38 0-73 9.5T560-454ZM260-320q47 0 91.5 10.5T440-278v-394q-41-24-87-36t-93-12q-36 0-71.5 7T120-692v396q35-12 69.5-18t70.5-6Zm260 42q44-21 88.5-31.5T700-320q36 0 70.5 6t69.5 18v-396q-33-14-68.5-21t-71.5-7q-47 0-93 12t-87 36v394Zm-40 118q-48-38-104-59t-116-21q-42 0-82.5 11T100-198q-21 11-40.5-1T40-234v-482q0-11 5.5-21T62-752q46-24 96-36t102-12q58 0 113.5 15T480-740q51-30 106.5-45T700-800q52 0 102 12t96 36q11 5 16.5 15t5.5 21v482q0 23-19.5 35t-40.5 1q-37-20-77.5-31T700-240q-60 0-116 21t-104 59ZM280-494Z",
    "title": "path://m504-292 92-70 92 70-34-114 92-74H632l-36-112-36 112H446l92 74-34 114ZM160-160q-33 0-56.5-23.5T80-240v-480q0-33 23.5-56.5T160-800h240l80 80h320q33 0 56.5 23.5T880-640v400q0 33-23.5 56.5T800-160H160Zm0-80h640v-400H447l-80-80H160v480Zm0 0v-480 480Z",
    "folder": "path://M160-160q-33 0-56.5-23.5T80-240v-480q0-33 23.5-56.5T160-800h240l80 80h320q33 0 56.5 23.5T880-640v400q0 33-23.5 56.5T800-160H160Zm0-80h640v-400H447l-80-80H160v480Zm0 0v-480 480Z",
    "file": "path://M320-240h320v-80H320v80Zm0-160h320v-80H320v80ZM240-80q-33 0-56.5-23.5T160-160v-640q0-33 23.5-56.5T240-880h320l240 240v480q0 33-23.5 56.5T720-80H240Zm280-520v-200H240v640h480v-440H520ZM240-800v200-200 640-640Z",
}

_SEMANTIC = ("author", "series", "franchise", "book", "title")


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
    hidden: frozenset[str] = frozenset(),
) -> dict:
    """Project a Subgraph to an ECharts `graph`-series options dict. Each node carries a per-kind
    glyph (`symbol`) so kind is legible without color; a thin warm-neutral border and a label halo
    keep nodes and labels readable on both the light and dark surfaces. Kinds in `hidden` are
    dropped from the projection (the focal node is always kept). No built-in legend — the UI
    renders its own bright-enabled / dim-struck-disabled legend."""
    visible = [
        nid for nid in sub.node_ids
        if nid == focal_id or display_kind(graph.nodes[nid]) not in hidden
    ]
    visible_set = set(visible)
    data = []
    for nid in visible:
        node = graph.nodes[nid]
        kind = display_kind(node)
        base = 20 if kind in ("author", "series", "franchise") else 14
        if nid == focal_id:
            item_style = {"borderColor": _FOCAL_RING, "borderWidth": 3,
                          "shadowColor": _FOCAL_GLOW, "shadowBlur": 10}
        else:
            item_style = {"borderColor": _NODE_BORDER, "borderWidth": 1}
        data.append({
            "id": nid,
            "name": label_of(node),
            "category": _KIND_INDEX[kind],
            "symbol": _KIND_SYMBOL[kind],
            "symbolSize": 34 if nid == focal_id else base,
            "value": confidence_of(node),
            "itemStyle": item_style,
        })
    links = [{
        "source": e.src, "target": e.dst,
        "lineStyle": {"type": "dashed"} if e.kind in ("author", "series", "franchise") else {"type": "solid"},
    } for e in sub.edges if e.src in visible_set and e.dst in visible_set]
    categories = [{"name": k, "itemStyle": {"color": KIND_COLOR[k]}} for k in KINDS]
    return {
        "tooltip": {},
        "series": [{
            "type": "graph", "layout": "force", "roam": True, "draggable": True,
            "label": {"show": True, "position": "right", "color": "#ffffff",
                      "textBorderColor": "rgba(28,25,22,0.85)", "textBorderWidth": 3},
            "force": {"repulsion": 130, "edgeLength": 80, "gravity": 0.05},
            "emphasis": {"focus": "adjacency"},
            "categories": categories, "data": data, "links": links,
        }],
    }
