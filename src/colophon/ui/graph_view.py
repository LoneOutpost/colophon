"""Graph page: a curation workbench over the entity graph build_graph produces for a
chosen scan root. A worklist header surfaces what needs review (the author?/series?
cohorts and any unclassified folders); the tree shows each folder's classification,
confidence, and provenance, with a per-node classify menu and bulk cohort confirm."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.graph_view import GraphTreeNode, graph_summary, graph_tree, grouping_cohort
from colophon.ui.chrome import body_column, page_header, page_toolbar

logger = logging.getLogger(__name__)

_LEGEND = (
    "Badges show each folder's classification and confidence (0 to 1). "
    "'author?' and 'series?' are suggestions you confirm; '· manual' marks a classification "
    "you confirmed. Confirmed authors and series apply to the books on the next scan."
)


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
                    ui.badge(b).props("outline").classes("colophon-chip")
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
            ui.badge(b).props("outline").classes("colophon-chip")


def _settled_line(s) -> str:
    """The secondary, already-classified counts, plural-aware, zeros dropped."""
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'s' if count != 1 else ''}"

    parts: list[str] = []
    if s.books:
        parts.append(n(s.books, "book"))
    if s.author_dirs:
        parts.append(n(s.author_dirs, "author"))
    if s.grouping_dirs:
        parts.append(n(s.grouping_dirs, "grouping"))
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

    state = {"root": str(roots[0])}

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
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Classify {node.label} as {kind}")
            name = ui.input("Name", value=node.label)
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Confirm",
                          on_click=lambda: dialog.submit(name.value or "")).props("no-caps")
        result = await dialog
        if result is not None:
            controller.set_node_classification(node.path, kind, result or None)
            ui.notify(f"Marked {node.label} as {kind}", type="positive")
            await _build()

    async def _clear_classify(node) -> None:
        controller.clear_node_classification(node.path)
        ui.notify(f"Cleared the classification on {node.label}")
        await _build()

    def _classify_menu(node) -> None:
        btn = ui.button(icon="sell").props(
            'flat dense round aria-label="Classify this folder"'
        ).classes("colophon-muted")
        btn.tooltip("Classify…")
        with btn, ui.menu():
            for kind in ("author", "series", "franchise", "container"):
                ui.menu_item(kind.capitalize(),
                             lambda kind=kind, node=node: _open_classify_dialog(node, kind))
            ui.separator()
            ui.menu_item("Clear", lambda node=node: _clear_classify(node))

    async def _confirm_cohort(hint: str, count: int) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Confirm {count} groupings as {hint}?")
            ui.label("Each folder is marked as that author/series; this applies to the "
                     "books on the next scan.").classes("colophon-muted text-caption")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Confirm", on_click=lambda: dialog.submit(True)).props("no-caps")
        if await dialog:
            n = controller.confirm_hint_cohort(Path(state["root"]), hint)
            ui.notify(f"Confirmed {n} folders as {hint}", type="positive")
            await _build()

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

    async def _load() -> None:
        cached = controller.cached_graph(Path(state["root"]), fresh=fresh_switch.value)
        if cached is not None:
            _show(cached)
        else:
            await _build()

    async def _on_root(value: str) -> None:
        state["root"] = value
        await _load()

    root_select.on_value_change(lambda e: _on_root(e.value))
    rebuild_btn.on_click(_build)
    fresh_switch.on_value_change(lambda e: _load())
    ui.timer(0.1, _load, once=True)  # initial: render the cached graph, or build the first time


def render_explorer(controller: AppController) -> None:
    """Read-only interactive neighborhood explorer over the library graph (ECharts)."""
    with ui.row().classes("w-full no-wrap gap-2"):
        with ui.column().classes("col"):
            search = ui.input(placeholder="Find author, series, or book…").props(
                "dense outlined clearable debounce=300"
            ).classes("w-full")
            results = ui.column().classes("w-full gap-0")
            chart = ui.echart({"series": [{"type": "graph", "data": [], "links": []}]}).classes(
                "w-full"
            ).style("height: 62vh")
            note = ui.label("").classes("text-caption colophon-muted")
        panel = ui.column().classes("col-4 gap-1")

    def _show_panel(focal: dict) -> None:
        panel.clear()
        with panel:
            if not focal:
                ui.label("Select a node to inspect it.").classes("text-caption colophon-muted")
                return
            ui.label(focal["label"]).classes("text-subtitle1")
            badge = focal["kind"].upper()
            if focal.get("confidence") is not None:
                badge += f" · {focal['confidence']:.0f}"
            ui.label(badge).classes("colophon-seccap")
            c = focal["connections"]
            ui.label(
                f"{c['parents']} parent · {c['children']} children · {c['series']} series"
            ).classes("text-caption colophon-muted")
            fields = focal.get("fields") or {}
            if fields:
                ui.label(f"author: {', '.join(fields.get('authors') or []) or '—'}").classes("text-caption")
                ui.label(f"series: {', '.join(fields.get('series') or []) or '—'}").classes("text-caption")
            files = focal.get("files") or []
            if files:
                ui.label(f"{len(files)} files").classes("colophon-seccap")
                for name in files[:20]:
                    ui.label(name).classes("text-caption colophon-muted ellipsis")
            with ui.row().classes("q-mt-sm").style("opacity:.45"):
                ui.label("Operations · 3.2").classes("text-caption")

    def _focus(focal_id: str) -> None:
        view = controller.graph_neighborhood(focal_id)
        chart.options = view["echart"]
        chart.update()
        _show_panel(view["focal"])
        omitted = view["omitted"]
        note.set_text(f"Showing a capped neighborhood — {omitted} more not shown." if omitted else "")

    def _on_node_click(e) -> None:
        # e.data is the clicked ECharts node dict; e.data_type distinguishes 'node' from 'edge'.
        if getattr(e, "data_type", None) == "node" and isinstance(e.data, dict) and e.data.get("id"):
            _focus(e.data["id"])

    chart.on_point_click(_on_node_click)

    def _run_search() -> None:
        results.clear()
        with results:
            for h in controller.graph_search(search.value or "")[:12]:
                ui.button(
                    f"{h['label']}  ·  {h['kind']}",
                    on_click=lambda _=None, i=h["id"]: _focus(i),
                ).props("flat dense no-caps align=left").classes("w-full")

    search.on("keydown.enter", lambda _=None: _run_search())
    search.on_value_change(lambda _=None: _run_search())
    _show_panel({})


def render_graph(controller: AppController) -> None:
    """/graph: an interactive neighborhood Explorer (default) or the Classic classification tree."""
    with page_header(controller, "graph", icon="account_tree"):
        pass
    mode = {"v": "explorer"}

    def _render() -> None:
        holder.clear()
        with holder:
            (render_explorer if mode["v"] == "explorer" else render_classic_tree)(controller)

    ui.toggle(
        {"explorer": "Explorer", "classic": "Classic tree"}, value=mode["v"],
        on_change=lambda e: (mode.update(v=e.value), _render()),
    ).props("dense no-caps").classes("q-ma-sm")
    holder = ui.column().classes("w-full")
    _render()
