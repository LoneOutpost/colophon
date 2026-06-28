"""Graph page: a diagnostic tree view of the entity graph build_graph produces for a
chosen scan root, with classification/role badges and summary counts."""

from __future__ import annotations

import asyncio
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
                ui.label(node.label)
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
    with page_header(controller, "graph", icon="account_tree"):
        roots = controller.graph_roots()
        if not roots:
            ui.label("No scan paths configured. Set them in Settings.").classes(
                "colophon-muted q-pa-md"
            )
            return

        state = {"root": str(roots[0])}
        summary = ui.label().classes("text-caption colophon-muted q-mb-sm")
        body = ui.column().classes("w-full")

        async def _rebuild() -> None:
            body.clear()
            summary.set_text("Building…")
            with body:
                spinner = ui.spinner(size="lg")
            graph = await asyncio.to_thread(controller.graph_for, Path(state["root"]))
            s = graph_summary(graph)
            roles = ", ".join(f"{n} {role}" for role, n in sorted(s.files_by_role.items()))
            summary.set_text(
                f"{s.directories} directories ({s.author_dirs} author) · "
                f"{s.books} books in {s.multi_book_dirs} multi-book folders · "
                f"files: {roles or 'none'}"
            )
            spinner.delete()
            tree = graph_tree(graph, Path(state["root"]))
            with body:
                if not tree:
                    ui.label("No books found under this root.").classes(
                        "colophon-muted q-pa-md"
                    )
                else:
                    for node in tree:
                        _render_node(node)

        async def _on_root(value: str) -> None:
            state["root"] = value
            await _rebuild()

        with ui.row().classes("items-center q-gutter-sm w-full no-wrap q-mb-sm"):
            ui.select(
                {str(r): str(r) for r in roots}, value=state["root"],
                on_change=lambda e: _on_root(e.value),
            ).props("dense outlined").classes("col")
            ui.button("Rebuild", icon="refresh", on_click=_rebuild).props("flat no-caps")

        ui.timer(0.1, _rebuild, once=True)  # initial build after the page mounts
