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

# Human type labels per display bucket. A classified directory reads "<Kind> Folder".
KIND_LABEL = {
    "author": "Author", "series": "Series", "franchise": "Franchise",
    "book": "Book", "title": "Title", "folder": "Folder", "file": "File",
}

# Semantic kinds that can occur on a directory node (a classified folder). Titles included;
# 'book' is never a directory facet (a book folder classifies as 'title').
_FOLDER_SEMANTICS = ("author", "series", "franchise", "title")

# Flattened, transform-free ECharts glyphs (folder base + classification hint, one path each).
# Source of truth: src/colophon/ui/assets/glyphs/<kind>-folder.svg. Regenerate with:
#   uv run --with svgelements python - <<'PY'
#   from svgelements import SVG, Path
#   for name in ("author-folder","series-folder","franchise-folder"):
#       svg = SVG.parse(f"src/colophon/ui/assets/glyphs/{name}.svg")
#       ds = [Path(el).d() for el in svg.elements() if isinstance(el, Path) and Path(el).d()]
#       print(name, "path://" + " ".join(ds))
#   PY
# 'title' reuses the folder+star glyph already in _KIND_SYMBOL["title"] (folder_special).
_FOLDER_KIND_SYMBOL = {
    "author": "path://m 10.12871624,12.26665106 q -0.67850234,-0.67850234 -0.67850234,-1.63129286 q 0,-0.95279052 0.67850234,-1.63129286 q 0.67850234,-0.67850234 1.63129286,-0.67850234 q 0.95279052,0 1.63129286,0.67850234 q 0.67850234,0.67850234 0.67850234,1.63129286 q 0,0.95279052 -0.67850234,1.63129286 q -0.67850234,0.67850234 -1.63129286,0.67850234 q -0.95279052,0 -1.63129286,-0.67850234 z m -2.98829754,5.29809274 l 0,-1.61685664 q 0,-0.49083148 0.25263385,-0.90226375 q 0.25263385,-0.41143227 0.67128423,-0.62797557 q 0.89504564,-0.44752282 1.81896372,-0.67128423 q 0.92391808,-0.22376141 1.8767086,-0.22376141 q 0.95279052,0 1.8767086,0.22376141 q 0.92391808,0.22376141 1.81896372,0.67128423 q 0.41865038,0.2165433 0.67128423,0.62797557 q 0.25263385,0.41143227 0.25263385,0.90226375 l 0,1.61685664 z m 1.1548976,-1.1548976 l 6.9293856,0 l 0,-0.46195904 q 0,-0.15879842 -0.07939921,-0.2887244 q -0.07939921,-0.12992598 -0.20932519,-0.20210708 q -0.77955588,-0.38977794 -1.57354798,-0.58466691 q -0.7939921,-0.19488897 -1.60242042,-0.19488897 q -0.80842832,0 -1.60242042,0.19488897 q -0.7939921,0.19488897 -1.57354798,0.58466691 q -0.12992598,0.0721811 -0.20932519,0.20210708 q -0.07939921,0.12992598 -0.07939921,0.2887244 z M 12.57565553,11.45100463 Q 12.9149067,11.11175346 12.9149067,10.6353582 Q 12.9149067,10.15896294 12.57565553,9.81971177 Q 12.23640436,9.4804606 11.7600091,9.4804606 q -0.47639526,0 -0.81564643,0.33925117 q -0.33925117,0.33925117 -0.33925117,0.81564643 q 0,0.47639526 0.33925117,0.81564643 q 0.33925117,0.33925117 0.81564643,0.33925117 q 0.47639526,0 0.81564643,-0.33925117 z M 11.7600091,10.6353582 Z m 0,5.774488 z M 4,20 q -0.825,0 -1.4125,-0.5875 T 2,18 l 0,-12 q 0,-0.825 0.5875,-1.4125 T 4,4 l 6,0 l 2,2 l 8,0 q 0.825,0 1.4125,0.5875 T 22,8 l 0,10 q 0,0.825 -0.5875,1.4125 T 20,20 L 4,20 Z m 0,-2 l 16,0 l 0,-10 L 11.175,8 l -2,-2 L 4,6 l 0,12 Z m 0,0 l 0,-12 l 0,12 Z",
    "series": "path://M 4,20 q -0.825,0 -1.4125,-0.5875 T 2,18 l 0,-12 q 0,-0.825 0.5875,-1.4125 T 4,4 l 6,0 l 2,2 l 8,0 q 0.825,0 1.4125,0.5875 T 22,8 l 0,10 q 0,0.825 -0.5875,1.4125 T 20,20 L 4,20 Z m 0,-2 l 16,0 l 0,-10 L 11.175,8 l -2,-2 L 4,6 l 0,12 Z m 0,0 l 0,-12 l 0,12 Z m 11.9999999525,17.7783369965 l -4.51101357,-3.50856611 l 0.8270191545,-0.6265296625 l 3.6839944155,2.856975261 l 3.6839944155,-2.856975261 l 0.8270191545,0.6265296625 z m 0,-2.5311798365 l -4.51101357,-3.50856611 l 4.51101357,-3.50856611 l 4.51101357,3.50856611 z m 0,-3.50856611 z m 0,2.2304455985 l 2.8820364475,-2.2304455985 l -2.8820364475,-2.2304455985 l -2.8820364475,2.2304455985 z",
    "franchise": "path://m 4,20 q -0.825,0 -1.4125,-0.5875 Q 2,18.825 2,18 l 0,-12 q 0,-0.825 0.5875,-1.4125 Q 3.175,4 4,4 l 6,0 l 2,2 l 8,0 q 0.825,0 1.4125,0.5875 q 0.5875,0.5875 0.5875,1.4125 l 0,10 q 0,0.825 -0.5875,1.4125 Q 20.825,20 20,20 Z m 0,-2 L 20,18 L 20,8 L 11.175,8 l -2,-2 L 4,6 Z m 0,0 l 0,-12 z m 10.12874363,14.85259712 l 5.61376932,0 l 0,-5.61376932 l -0.93562822,0 l 0,3.27469877 l -1.169535275,-0.701721165 l -1.169535275,0.701721165 L 12.46781418,9.2388278 L 10.12874363,9.2388278 Z m 0,0.93562822 q -0.38594664075,0 -0.660787430375,-0.274840789625 Q 9.19311541,15.2385437607 9.19311541,14.85259712 l 0,-5.61376932 q 0,-0.38594664075 0.274840789625,-0.660787430375 Q 9.74279698925,8.30319958 10.12874363,8.30319958 l 5.61376932,0 q 0.38594664075,0 0.660787430375,0.274840789625 q 0.274840789625,0.274840789625 0.274840789625,0.660787430375 l 0,5.61376932 q 0,0.38594664075 -0.274840789625,0.660787430375 Q 16.1284595908,15.78822534 15.74251295,15.78822534 Z M 8.25748719,17.65948178 q -0.38594664075,0 -0.660787430375,-0.274840789625 Q 7.32185897,17.1098002007 7.32185897,16.72385356 l 0,-6.54939754 l 0.93562822,0 l 0,6.54939754 l 6.54939754,0 l 0,0.93562822 z m 4.21032699,-8.42065398 l 2.33907055,0 z m -2.33907055,0 l 5.61376932,0 z",
    "title": _KIND_SYMBOL["title"],
}


def filter_bucket(node: NodeRecord) -> str:
    """Legend/hide/category bucket. Physical-first: every directory (classified or not) buckets
    as 'folder' and a file as 'file', so the Folder toggle hides all folders; only purely-logical
    nodes use their semantic kind. Mirrors graph_inspect._panel_kind's folder framing."""
    if node.physical == "directory":
        return "folder"
    if node.physical == "file":
        return "file"
    return node.semantic if node.semantic in _SEMANTIC else "folder"


def type_label(node: NodeRecord) -> str:
    """Human type caption. A classified directory reads '<Kind> Folder' so it is distinct from
    the entity node of the same kind (an author folder vs the author entity)."""
    if node.physical == "directory" and node.semantic in _FOLDER_SEMANTICS:
        return f"{KIND_LABEL[node.semantic]} Folder"
    return KIND_LABEL[filter_bucket(node)]


def node_glyph(node: NodeRecord) -> str:
    """ECharts symbol path. A classified directory uses the folder-family glyph for its kind;
    everything else uses the plain per-bucket glyph."""
    if node.physical == "directory" and node.semantic in _FOLDER_SEMANTICS:
        return _FOLDER_KIND_SYMBOL[node.semantic]
    return _KIND_SYMBOL[filter_bucket(node)]


def node_tint(node: NodeRecord) -> str:
    """Fill color. Classification color when classified (an author folder stays terracotta),
    otherwise the neutral bucket color."""
    # _SEMANTIC includes "book", but _SEMANTIC_DIR_KINDS in graph_records.py ensures a directory
    # never carries semantic="book", so KIND_COLOR["book"] is only reached for logical book nodes.
    if node.semantic in _SEMANTIC:
        return KIND_COLOR[node.semantic]
    return KIND_COLOR[filter_bucket(node)]


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
        if nid == focal_id or filter_bucket(graph.nodes[nid]) not in hidden
    ]
    visible_set = set(visible)
    data = []
    for nid in visible:
        node = graph.nodes[nid]
        bucket = filter_bucket(node)
        base = 20 if bucket in ("author", "series", "franchise") else 14
        if nid == focal_id:
            item_style = {"color": node_tint(node), "borderColor": _FOCAL_RING, "borderWidth": 3,
                          "shadowColor": _FOCAL_GLOW, "shadowBlur": 10}
        else:
            item_style = {"color": node_tint(node), "borderColor": _NODE_BORDER, "borderWidth": 1}
        data.append({
            "id": nid,
            "name": label_of(node),
            "category": _KIND_INDEX[bucket],
            "symbol": node_glyph(node),
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
