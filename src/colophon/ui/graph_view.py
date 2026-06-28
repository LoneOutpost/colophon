"""Graph page: a diagnostic tree view of the entity graph build_graph produces for a
chosen scan root, with classification/role badges and summary counts."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.graph_view import GraphTreeNode, graph_summary, graph_tree, grouping_cohort
from colophon.ui.chrome import page_header


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
    cohort_actions = ui.row().classes("items-center q-gutter-sm q-px-sm q-mb-sm")
    body = ui.column().classes("w-full q-px-sm")

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
            await _build()

    async def _clear_classify(node) -> None:
        controller.clear_node_classification(node.path)
        await _build()

    def _classify_menu(node) -> None:
        with ui.button(icon="sell").props("flat dense round").classes("colophon-muted"):
            with ui.menu():
                for kind in ("author", "series", "franchise", "container"):
                    ui.menu_item(kind.capitalize(),
                                 lambda kind=kind, node=node: _open_classify_dialog(node, kind))
                ui.separator()
                ui.menu_item("Clear", lambda node=node: _clear_classify(node))

    async def _confirm_cohort(hint: str, count: int) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Confirm {count} groupings as {hint}?")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Confirm", on_click=lambda: dialog.submit(True)).props("no-caps")
        if await dialog:
            controller.confirm_hint_cohort(Path(state["root"]), hint)
            await _build()

    def _show(graph) -> None:
        s = graph_summary(graph)
        roles = ", ".join(f"{n} {role}" for role, n in sorted(s.files_by_role.items()))
        summary.set_text(
            f"{s.directories} directories · "
            f"{s.author_dirs} author · "
            f"{s.grouping_dirs} grouping "
            f"({s.grouping_author_hint} author? · {s.grouping_series_hint} series? · "
            f"{s.grouping_ambiguous_hint} ambiguous?) · "
            f"{s.container_dirs} container · {s.title_dirs} title · "
            f"{s.unknown_dirs} unknown · {s.books} books · files: {roles or 'none'}"
        )
        cohort_actions.clear()
        with cohort_actions:
            for hint in ("author", "series"):
                cohort = grouping_cohort(graph, root=Path(state["root"]), hint=hint)
                if cohort:
                    count = len(cohort)
                    ui.button(
                        f"Confirm {count} {hint}",
                        on_click=lambda hint=hint, count=count: _confirm_cohort(hint, count),
                    ).props("flat dense no-caps").classes("colophon-chip")
        body.clear()
        tree = graph_tree(graph, Path(state["root"]))
        with body:
            if not tree:
                ui.label("No books found under this root.").classes("colophon-muted q-pa-md")
            else:
                for node in tree:
                    _render_node(node, _classify_menu)

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
