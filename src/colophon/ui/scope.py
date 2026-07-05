"""The shared 'apply to' scope control used by the Match and Persist actions: a segmented
Selected / Ready / All toggle with live counts. Both actions resolve the choice through
`controller.books_for_scope(value, selected_ids)`."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController


def scope_selector(controller: AppController, selected_ids: set[str]) -> ui.toggle:
    """Render the Selected / Ready / All toggle with counts and return it (read `.value`).
    Defaults to Selected when there's a selection, else Ready."""
    stats = controller.dashboard_stats()
    n_sel, n_ready, n_all = len(selected_ids), stats.get("ready", 0), stats.get("total", 0)
    options = {
        "selected": f"Selected · {n_sel}",
        "ready": f"Ready · {n_ready}",
        "all": f"All · {n_all}",
    }
    initial = "selected" if n_sel else "ready"
    return ui.toggle(options, value=initial).props("no-caps").classes("colophon-seg")
