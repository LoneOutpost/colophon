"""Graph page: a curation workbench over the entity graph build_graph produces for a
chosen scan root. A worklist header surfaces what needs review (the author?/series?
cohorts and any unclassified folders); the tree shows each folder's classification,
confidence, and provenance, with a per-node classify menu and bulk cohort confirm."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

from nicegui import app, ui

from colophon.controller import AppController
from colophon.core.graph import DirectoryNode
from colophon.core.graph_explore import KIND_COLOR, KIND_ICON, KIND_LABEL, KINDS
from colophon.core.graph_records import book_node_id
from colophon.core.graph_view import GraphTreeNode, graph_summary, graph_tree, grouping_cohort
from colophon.ui.chrome import body_column, empty_state, page_header, page_toolbar
from colophon.ui.dialogs import modal

logger = logging.getLogger(__name__)

_CLASSIFICATION_BADGE_TIP = (
    "Folder classification. A trailing ? means tentative; · manual means you set it."
)

_LEGEND = (
    "Badges show each folder's classification and confidence (0 to 1). "
    "'author?' and 'series?' are suggestions you confirm; '· manual' marks a classification "
    "you confirmed. Confirmed authors and series apply to the books on the next scan."
)

# Manual-classification targets (label shown in the menus), ordered Book-first since correcting a
# book misread as an author is the common case. Book is stored as the `title` kind; author/series/
# franchise carry the folder name as their entity value, book/container carry none.
_CLASSIFY_KINDS = {
    "title": "Book", "author": "Author", "series": "Series",
    "franchise": "Franchise", "container": "Container",
}
_KINDS_WITH_VALUE = ("author", "series", "franchise")


def reclassify_folder_dialog(controller: AppController, path: Path, current_kind: str, *,
                             on_done=None) -> None:
    """A small dialog to manually reclassify the folder at `path` (Book / Author / Series /
    Franchise / Container) or clear the override. Shared by the Nodes explorer and the book detail
    pane. `on_done` runs after the change (awaited if it returns a coroutine) so the caller can
    refresh its view."""
    async def _finish(message: str) -> None:
        dialog.close()
        ui.notify(message, type="positive")
        if on_done is not None:
            result = on_done()
            if asyncio.iscoroutine(result):
                await result

    async def _apply(kind: str) -> None:
        controller.set_node_classification(
            path, kind, path.name if kind in _KINDS_WITH_VALUE else None)
        await _finish(f"Marked {path.name} as {_CLASSIFY_KINDS[kind].lower()}")

    async def _clear() -> None:
        controller.clear_node_classification(path)
        await _finish(f"Cleared the classification on {path.name}")

    current = (KIND_LABEL.get(current_kind, current_kind) or "unclassified").lower()
    with modal() as dialog, ui.card().classes("w-72"):
        ui.label("Reclassify folder").classes("text-subtitle1")
        ui.label(f"{path.name}: currently {current}").classes("text-caption colophon-muted")
        with ui.column().classes("w-full gap-1 q-mt-xs"):
            for kind, label in _CLASSIFY_KINDS.items():
                ui.button(label, on_click=lambda k=kind: _apply(k)).props(
                    "flat no-caps align=left").classes("w-full")
            ui.separator()
            ui.button("Clear classification", on_click=_clear).props(
                "flat no-caps align=left").classes("w-full colophon-muted")
    dialog.open()


def _render_node(node: GraphTreeNode, on_classify=None) -> None:
    if node.node_kind == "dir":
        exp = ui.expansion().props("dense").classes("w-full")
        with exp.add_slot("header"):
            with ui.row().classes("items-center q-gutter-xs no-wrap"):
                ui.icon("folder", color="amber-7")
                lbl = ui.label(node.label)
                if node.tooltip:
                    lbl.tooltip(node.tooltip)
                for b in node.badges:
                    ui.badge(b).props("outline").classes("colophon-chip").tooltip(
                        _CLASSIFICATION_BADGE_TIP
                    )
                if on_classify is not None and node.path is not None:
                    on_classify(node)
        with exp:
            for child in node.children:
                _render_node(child, on_classify)
        return
    icon = "menu_book" if node.node_kind == "book" else "insert_drive_file"
    with ui.row().classes("items-center q-gutter-xs no-wrap q-ml-lg"):
        ui.icon(icon, color="primary" if node.node_kind == "book" else "grey-6")
        ui.label(node.label)
        for b in node.badges:
            ui.badge(b).props("outline").classes("colophon-chip").tooltip(
                _CLASSIFICATION_BADGE_TIP
            )


def _settled_line(s) -> str:
    """The secondary, already-classified counts, plural-aware, zeros dropped."""
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'s' if count != 1 else ''}"

    parts: list[str] = []
    if s.books:
        parts.append(n(s.books, "book"))
    if s.author_dirs:
        parts.append(n(s.author_dirs, "author"))
    if s.series_dirs:
        parts.append(n(s.series_dirs, "series"))
    if s.container_dirs:
        parts.append(n(s.container_dirs, "container"))
    if s.manual_dirs:
        parts.append(f"{s.manual_dirs} confirmed")
    return " · ".join(parts)


def render_classic_tree(controller: AppController) -> None:
    """The original classification tree, kept behind the Explorer/Classic toggle."""
    roots = controller.graph_roots()
    if not roots:
        ui.label("No scan paths configured. Set them in Settings.").classes(
            "colophon-muted q-pa-md"
        )
        return

    _ROOT_KEY = "graph_root"

    def _tab_storage():
        """app.storage.tab, or None when the client isn't connected. Remembering the root is
        best-effort — a transient no-connection must never raise and 500 the page."""
        try:
            return app.storage.tab
        except RuntimeError:
            return None

    state = {"root": str(roots[0])}
    _store = _tab_storage()
    _remembered = _store.get(_ROOT_KEY) if _store is not None else None
    if _remembered in {str(r) for r in roots}:  # restore the last-selected root across page navigation
        state["root"] = _remembered

    with page_toolbar():
        with ui.row().classes("items-center q-gutter-sm w-full no-wrap"):
            root_select = ui.select(
                {str(r): str(r) for r in roots}, value=state["root"],
            ).props("dense outlined").classes("col")
            fresh_switch = ui.switch("From scratch").props("dense")
            fresh_switch.tooltip("Ignore saved book data and build only from the files on disk.")
            help_btn = ui.button(icon="help_outline").props(
                'flat dense round aria-label="What the badges mean"'
            ).classes("colophon-muted")
            help_btn.tooltip(_LEGEND)
            rebuild_btn = ui.button("Rebuild", icon="refresh").props("flat no-caps")
        worklist = ui.column().classes("w-full q-gutter-xs")

    body = body_column("full")

    async def _open_classify_dialog(node, kind: str) -> None:
        label = _CLASSIFY_KINDS[kind]
        with modal() as dialog, ui.card():
            ui.label(f"Classify {node.label} as {label.lower()}")
            # Only the entity kinds carry a name (which author/series/franchise); Book and Container
            # take none, so no name field for them.
            name = ui.input("Name", value=node.label) if kind in _KINDS_WITH_VALUE else None
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Confirm", on_click=lambda: dialog.submit(True)).props("no-caps")
        if await dialog:
            controller.set_node_classification(
                node.path, kind, (name.value or None) if name is not None else None)
            ui.notify(f"Marked {node.label} as {label.lower()}", type="positive")
            await _render_maintained()

    async def _clear_classify(node) -> None:
        controller.clear_node_classification(node.path)
        ui.notify(f"Cleared the classification on {node.label}")
        await _render_maintained()

    async def _quick_classify(node, kind: str) -> None:
        """Right-click fast path: re-categorize `node` as `kind` at once, using the folder's own name
        as the value for the entity kinds (a book/container carries none). Recoverable via Clear."""
        value = node.label if kind in _KINDS_WITH_VALUE else None
        controller.set_node_classification(node.path, kind, value)
        ui.notify(f"Marked {node.label} as {_CLASSIFY_KINDS[kind].lower()}", type="positive")
        await _render_maintained()

    def _classify_menu(node) -> None:
        # Right-click anywhere on the row for fast re-categorization (value = the folder's own name);
        # the kebab opens the dialog for the rarer case where the value should differ from the name.
        with ui.context_menu():
            for kind, label in _CLASSIFY_KINDS.items():
                ui.menu_item(f"Mark as {label.lower()}",
                             lambda kind=kind, node=node: _quick_classify(node, kind))
            ui.separator()
            ui.menu_item("Clear classification", lambda node=node: _clear_classify(node))
            ui.separator()
            ui.menu_item(
                "Show in nodes",
                lambda node=node: ui.navigate.to(
                    _mode_url("explorer", DirectoryNode.id_for(node.path))),
            )
        btn = ui.button(icon="sell").props(
            'flat dense round aria-label="Classify this folder"'
        ).classes("colophon-muted")
        btn.tooltip("Classify…")
        with btn, ui.menu():
            for kind, label in _CLASSIFY_KINDS.items():
                ui.menu_item(label,
                             lambda kind=kind, node=node: _open_classify_dialog(node, kind))
            ui.separator()
            ui.menu_item("Clear", lambda node=node: _clear_classify(node))

    async def _confirm_cohort(hint: str, count: int) -> None:
        with modal() as dialog, ui.card():
            ui.label(f"Confirm {count} groupings as {hint}?")
            ui.label("Each folder is marked as that author/series; this applies to the "
                     "books on the next scan.").classes("colophon-muted text-caption")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Confirm", on_click=lambda: dialog.submit(True)).props("no-caps")
        if await dialog:
            n = controller.confirm_hint_cohort(Path(state["root"]), hint)
            ui.notify(f"Confirmed {n} folders as {hint}", type="positive")
            await _render_maintained()

    def _render_worklist(graph, s) -> None:
        worklist.clear()
        root = Path(state["root"])
        with worklist:
            author_cohort = grouping_cohort(graph, root=root, hint="author")
            series_cohort = grouping_cohort(graph, root=root, hint="series")
            if author_cohort or series_cohort or s.unknown_dirs:
                with ui.row().classes("items-center q-gutter-sm no-wrap"):
                    ui.label("Needs review").classes("text-weight-medium")
                    for hint, cohort in (("author", author_cohort), ("series", series_cohort)):
                        if cohort:
                            n = len(cohort)
                            ui.button(
                                f"Confirm {n} {hint}", icon="done_all",
                                on_click=lambda hint=hint, n=n: _confirm_cohort(hint, n),
                            ).props("flat dense no-caps").classes("text-primary")
                    if s.unknown_dirs:
                        ui.label(f"· {s.unknown_dirs} unclassified").classes("colophon-muted")
                ui.label("Confirmed authors and series apply to the books on the next scan.").classes(
                    "colophon-muted text-caption"
                )
            else:
                with ui.row().classes("items-center q-gutter-xs no-wrap"):
                    ui.icon("check_circle", color="positive")
                    ui.label("Everything is classified. Nothing needs your review.")
            settled = _settled_line(s)
            if settled:
                ui.label(settled).classes("colophon-muted text-caption")

    def _show(graph) -> None:
        _render_worklist(graph, graph_summary(graph))
        body.clear()
        tree = graph_tree(graph, Path(state["root"]))
        with body:
            if not tree:
                ui.label("No books found under this root.").classes("colophon-muted q-pa-md")
            else:
                for node in tree:
                    _render_node(node, _classify_menu)

    async def _build() -> None:
        worklist.clear()
        body.clear()
        with body, ui.row().classes("items-center q-gutter-sm q-pa-md"):
            ui.spinner(size="lg")
            prog = ui.label("Building…").classes("text-caption colophon-muted").props(
                "role=status aria-live=polite"
            )

        def _progress(done: int, total: int, label: str) -> None:
            prog.set_text(f"Building {done} / {total} · {label}")

        try:
            graph = await controller.graph_for_streamed(
                Path(state["root"]), fresh=fresh_switch.value, progress=_progress)
        except Exception as exc:  # surface any build failure as a retryable state (BLE001 intentional)
            logger.exception(f"graph build failed for {state['root']}")
            worklist.clear()
            body.clear()
            with body, ui.column().classes("q-pa-md q-gutter-sm"):
                ui.label("Couldn't build the graph for this root.").classes("text-weight-medium")
                ui.label(str(exc)).classes("colophon-muted text-caption")
                ui.button("Retry", icon="refresh", on_click=_build).props("flat no-caps")
            return
        _show(graph)

    def _loading(msg: str = "Loading…") -> None:
        worklist.clear()
        body.clear()
        with body, ui.row().classes("items-center q-gutter-sm q-pa-md"):
            ui.spinner(size="lg")
            ui.label(msg).classes("text-caption colophon-muted").props("role=status aria-live=polite")

    async def _render_maintained() -> None:
        """Show a loading state, then render the tree from the re-classified maintained graph (an
        in-memory reclassify that can take a moment on a large root). Falls back to a disk build when
        the root has no maintained graph yet."""
        _loading()
        await asyncio.sleep(0.05)  # let the spinner paint before the synchronous reclassify
        graph = controller.classic_tree_graph(Path(state["root"]))
        if graph.directories:
            _show(graph)
        else:
            await _build()  # nothing maintained for this root yet; build from disk

    async def _load() -> None:
        if fresh_switch.value:
            await _build()  # "From scratch": reconcile the graph with the filesystem
        else:
            await _render_maintained()

    async def _on_root(value: str) -> None:
        state["root"] = value
        store = _tab_storage()
        if store is not None:
            store[_ROOT_KEY] = value  # remember the selected root across page navigation
        await _load()

    root_select.on_value_change(lambda e: _on_root(e.value))
    rebuild_btn.on_click(_build)
    fresh_switch.on_value_change(lambda e: _load())
    ui.timer(0.1, _load, once=True)  # initial: render the cached graph, or build the first time


def _parse_hidden(hide: str | None) -> frozenset[str]:
    """Parse a `?hide=` CSV into the set of hidden display kinds, dropping anything unknown."""
    known = set(KINDS)
    return frozenset(k for k in (hide or "").split(",") if k in known)


def _parse_depth(depth: str | float | None) -> int:
    """Parse a depth (the `?depth=` query string or the spinner's numeric value), clamped to
    [1, 3]; anything invalid falls back to 1."""
    try:
        return max(1, min(3, int(depth)))
    except (TypeError, ValueError):
        return 1


def _graph_url(focal_id: str, hidden: frozenset[str], *, depth: int = 1) -> str:
    """The explorer URL for a focal node, hidden-kind set, and depth. `depth` is omitted when 1 and
    `hide` when empty, for clean links; `hide` is a sorted, un-encoded CSV so `_parse_hidden` round-
    trips it."""
    url = f"/graph?focal={quote(focal_id)}"
    if depth != 1:
        url += f"&depth={depth}"
    if hidden:
        url += f"&hide={','.join(sorted(hidden))}"
    return url


def nodes_url_for_book(book_id: str) -> str:
    """The /graph Nodes-explorer URL focused on a book's node — for the detail pane's
    'Show in the graph' jump. Keeps the explorer URL format in one place."""
    return _mode_url("explorer", book_node_id(book_id))


def _mode_url(mode: str, focal: str | None) -> str:
    """A /graph URL for `mode` (nodes=explorer / tree=classic) that carries the focal node across the
    flip, so switching Tree<->Nodes keeps you on the same node."""
    url = f"/graph?mode={mode}"
    if focal:
        url += f"&focal={quote(focal)}"
    return url


def _node_click_target(args: dict, hidden: frozenset[str], *, depth: int = 1) -> str | None:
    """The explorer URL to navigate to for an ECharts `componentClick`, or None to ignore it. Only
    graph *node* clicks navigate; edge and background clicks are ignored. We read `args` defensively
    (instead of NiceGUI's `on_point_click`) because ECharts omits `value` from edge-click payloads,
    which makes NiceGUI's built-in handler raise `KeyError: 'value'`."""
    if args.get("componentType") != "series" or args.get("dataType") != "node":
        return None
    data = args.get("data")
    if isinstance(data, dict) and data.get("id"):
        return _graph_url(str(data["id"]), hidden, depth=depth)
    return None


def _explorer_legend(focal_id: str, hidden: frozenset[str], depth: int = 1) -> None:
    """A compact legend row under the chart: one glyph + label per kind. Enabled entries are bright;
    hidden entries are dimmed and struck through. Clicking an entry toggles that kind and re-renders
    via the URL (in-place ECharts updates don't propagate in this NiceGUI/ECharts combo)."""
    with ui.row().classes("items-center gap-4 q-mt-sm flex-wrap"):
        for kind in KINDS:
            is_hidden = kind in hidden
            new_hidden = (hidden - {kind}) if is_hidden else (hidden | {kind})
            target = _graph_url(focal_id, new_hidden, depth=depth)
            entry = ui.row().classes("items-center gap-1 cursor-pointer").style(
                "opacity:.4" if is_hidden else "opacity:1"
            )
            entry.on("click", lambda _=None, t=target: ui.navigate.to(t))
            entry.tooltip(f"{'Show' if is_hidden else 'Hide'} {kind} nodes")
            with entry:
                ui.icon(KIND_ICON[kind], size="1.75rem").style(f"color:{KIND_COLOR[kind]}")
                label = ui.label(KIND_LABEL[kind]).classes("text-caption")
                if is_hidden:
                    label.style("text-decoration: line-through")


def _explorer_panel(view, focal_id: str | None, *, on_reclassify=None) -> None:
    """Render one focal node's inspect read-model (a NodeInspection) into the current container.
    When `on_reclassify` is given (the focal node is a classifiable folder) a Reclassify action is
    offered, so the read-only Nodes view can still correct a misclassified folder."""
    if not view or not view.kind:
        ui.label("Search for an author, series, or book, then click a result to explore it.").classes(
            "text-caption colophon-muted"
        )
        return
    with ui.row().classes("items-center justify-between no-wrap w-full"):
        ui.label(view.label).classes("text-subtitle1")
        with ui.row().classes("items-center no-wrap q-gutter-xs"):
            if on_reclassify is not None:
                ui.button("Reclassify", icon="sell", on_click=on_reclassify).props(
                    "flat dense no-caps").classes("colophon-muted").tooltip(
                    "Change this folder's classification (right-click a node does the same)")
            if focal_id:
                ui.button("Show in tree", icon="account_tree",
                          on_click=lambda: ui.navigate.to(_mode_url("classic", focal_id))).props(
                    "flat dense no-caps").classes("colophon-muted")
    cap = view.type_caption.upper()
    if view.confidence is not None:
        cap += f" · {view.confidence:.0f}%"
    ui.label(cap).classes("colophon-seccap")
    for label, value in view.rows:
        ui.label(f"{label}: {value}").classes("text-caption")
    if view.linked_folders:
        ui.label(f"Linked folders: {', '.join(view.linked_folders)}").classes("text-caption")
    if view.files:
        ui.label(f"{len(view.files)} files").classes("colophon-seccap q-mt-sm")
        for name in view.files[:20]:
            ui.label(name).classes("text-caption colophon-muted ellipsis")
    if view.provenance:
        ui.label("Provenance").classes("colophon-seccap q-mt-sm")
        for line in view.provenance:
            ui.label(line).classes("text-caption colophon-muted")
    if view.links:
        with ui.row().classes("q-mt-sm gap-2 flex-wrap"):
            for link in view.links:
                ui.button(link.label, on_click=lambda _=None, u=link.url: ui.navigate.to(u)).props(
                    "flat dense no-caps"
                )
    with ui.row().classes("q-mt-sm").style("opacity:.45"):
        ui.label("Operations · 3.2").classes("text-caption")


def render_explorer(controller: AppController, focal_id: str | None,
                    hidden: frozenset[str] = frozenset(), depth: int = 1) -> None:
    """Read-only neighborhood explorer. URL-driven: focal (`?focal=`), depth (`?depth=`), and the
    hidden-kind set (`?hide=`) all live in the query string, so every change is a fresh full render
    (NiceGUI's `ui.echart` only renders a graph built with data at construction; NiceGUI 3.13 +
    bundled ECharts 6). Every focal/filter change is therefore a full render."""
    chart_view = controller.graph_neighborhood(focal_id, hops=depth, hidden=hidden) if focal_id else None

    with ui.row().classes("w-full no-wrap gap-2"):
        with ui.column().classes("col"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                search = ui.input(placeholder="Find author, series, or book…").props(
                    "dense outlined clearable debounce=300"
                ).classes("col")
                depth_input = ui.number(value=depth, min=1, max=3, step=1).props(
                    'dense outlined label="Depth"'
                ).style("width:6rem")
                depth_input.on_value_change(
                    lambda e: focal_id and ui.navigate.to(
                        _graph_url(focal_id, hidden, depth=_parse_depth(e.value))
                    )
                )
            results = ui.column().classes("w-full gap-0")
            if chart_view is not None:
                # Built WITH data at construction so ECharts actually renders a canvas.
                chart = ui.echart(chart_view["echart"]).classes("w-full").style("height: 62vh")

                def _on_node_click(e) -> None:
                    target = _node_click_target(e.args, hidden, depth=depth)
                    if target:
                        ui.navigate.to(target)

                chart.on("componentClick", _on_node_click, ["componentType", "dataType", "data"])

                def _on_node_contextmenu(e) -> None:
                    args = e.args or {}
                    if args.get("componentType") != "series" or args.get("dataType") != "node":
                        return
                    data = args.get("data")
                    nid = data.get("id") if isinstance(data, dict) else None
                    resolved = controller.directory_node(str(nid)) if nid else None
                    if resolved is None:
                        return  # only folders are classifiable; ignore book/entity right-clicks
                    reclassify_folder_dialog(
                        controller, resolved[0], resolved[1],
                        on_done=lambda i=str(nid): ui.navigate.to(_graph_url(i, hidden, depth=depth)))

                chart.on("contextmenu", _on_node_contextmenu, ["componentType", "dataType", "data"])
                omitted = chart_view["omitted"]
                if omitted:
                    ui.label(f"Showing a capped neighborhood — {omitted} more not shown.").classes(
                        "text-caption colophon-muted"
                    )
                _explorer_legend(focal_id, hidden, depth)
            else:
                with empty_state(
                    "hub", "Explore the library graph",
                    "Search for an author, series, or book above, then click a result to "
                    "see how it connects across authors, series, and franchises.",
                ):
                    pass
        with ui.column().classes("col-4 gap-1"):
            recl = controller.directory_node(focal_id) if focal_id else None

            def _open_reclassify(r=recl) -> None:
                reclassify_folder_dialog(
                    controller, r[0], r[1],
                    on_done=lambda: ui.navigate.to(_graph_url(focal_id, hidden, depth=depth)))

            _explorer_panel(
                controller.graph_inspect(focal_id) if focal_id else None, focal_id,
                on_reclassify=_open_reclassify if recl else None,
            )

    def _run_search() -> None:
        results.clear()
        with results:
            for h in controller.graph_search(search.value or "")[:12]:
                ui.button(
                    f"{h['label']}  ·  {h['kind']}",
                    on_click=lambda _=None, i=h["id"]: ui.navigate.to(_graph_url(i, hidden, depth=depth)),
                ).props("flat dense no-caps align=left").classes("w-full")

    search.on("keydown.enter", lambda _=None: _run_search())
    search.on_value_change(lambda _=None: _run_search())


def render_graph(
    controller: AppController, *, mode: str = "explorer",
    focal: str | None = None, hide: str | None = None, depth: str | None = None,
) -> None:
    """/graph: an interactive neighborhood Explorer (default) or the Classic classification tree.
    `mode`, `focal`, `hide`, and `depth` come from the URL query string so every view is a stable
    full render."""
    with page_header(controller, "graph"):
        pass
    ui.toggle(
        {"explorer": "Nodes", "classic": "Tree"}, value=mode,
        on_change=lambda e: ui.navigate.to(_mode_url(e.value, focal)),
    ).props("dense no-caps").classes("q-ma-sm")
    if mode == "classic":
        render_classic_tree(controller)
    else:
        render_explorer(controller, focal, _parse_hidden(hide), _parse_depth(depth))
