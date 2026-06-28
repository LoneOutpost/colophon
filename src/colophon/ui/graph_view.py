"""Graph page: a diagnostic tree view of the entity graph build_graph produces for a
chosen scan root, with classification/role badges and summary counts."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.graph_view import GraphTreeNode, graph_summary, graph_tree
from colophon.ui.chrome import page_header


def _render_node(node: GraphTreeNode) -> None:
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
        with exp:
            for child in node.children:
                _render_node(child)
        return
    icon = "menu_book" if node.node_kind == "book" else "insert_drive_file"
    with ui.row().classes("items-center q-gutter-xs no-wrap q-ml-lg"):
        ui.icon(icon, color="primary" if node.node_kind == "book" else "grey-6")
        ui.label(node.label)
        for b in node.badges:
            ui.badge(b).props("outline").classes("colophon-chip")


def render_graph(controller: AppController) -> None:
    # page_header's `with` body is for header action buttons only; the page content
    # belongs after it (matching every other page), or it renders inside the header.
    with page_header(controller, "graph", icon="account_tree"):
        pass

    roots = controller.graph_roots()
    if not roots:
        ui.label("No scan paths configured. Set them in Settings.").classes(
            "colophon-muted q-pa-md"
        )
        return

    state = {"root": str(roots[0])}

    with ui.row().classes("items-center q-gutter-sm w-full no-wrap q-pa-sm"):
        root_select = ui.select(
            {str(r): str(r) for r in roots}, value=state["root"],
        ).props("dense outlined").classes("col")
        fresh_switch = ui.switch("From scratch").props("dense")
        fresh_switch.tooltip("Ignore saved book data and build only from the files on disk.")
        rebuild_btn = ui.button("Rebuild", icon="refresh").props("flat no-caps")

    summary = ui.label().classes("text-caption colophon-muted q-px-sm q-mb-sm")
    body = ui.column().classes("w-full q-px-sm")

    def _show(graph) -> None:
        s = graph_summary(graph)
        roles = ", ".join(f"{n} {role}" for role, n in sorted(s.files_by_role.items()))
        summary.set_text(
            f"{s.directories} directories · "
            f"{s.author_dirs} author · {s.grouping_dirs} grouping · "
            f"{s.container_dirs} container · {s.title_dirs} title · "
            f"{s.unknown_dirs} unknown · {s.books} books · files: {roles or 'none'}"
        )
        body.clear()
        tree = graph_tree(graph, Path(state["root"]))
        with body:
            if not tree:
                ui.label("No books found under this root.").classes("colophon-muted q-pa-md")
            else:
                for node in tree:
                    _render_node(node)

    async def _build() -> None:
        body.clear()
        summary.set_text("Building…")
        with body:
            with ui.row().classes("items-center q-gutter-sm q-pa-md"):
                ui.spinner(size="lg")
                prog = ui.label("Building…").classes("text-caption colophon-muted")

        def _progress(done: int, total: int, label: str) -> None:
            prog.set_text(f"Building {done} / {total} · {label}")

        graph = await controller.graph_for_streamed(
            Path(state["root"]), fresh=fresh_switch.value, progress=_progress)
        _show(graph)  # clears body (removing the spinner row) and renders the tree

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
