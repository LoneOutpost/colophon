"""The shared 'apply to' scope control used by the Match and Persist actions: a segmented
Selected / <ready tier> / All toggle with live counts. Both actions resolve the choice through
`controller.books_for_scope(value, selected_ids, ready_state=...)`; the middle tier is READY for
Persist ('Ready') and IDENTIFIED for Match ('Identified')."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController
from colophon.core.models import BookState


def scope_selector(
    controller: AppController, selected_ids: set[str],
    *, ready_label: str = "Ready", ready_state: BookState = BookState.READY,
) -> ui.toggle:
    """Render the Selected / <ready_label> / All toggle with counts and return it (read `.value`).
    The middle tier counts `ready_state`. Defaults to Selected when there's a selection, else the
    middle tier."""
    counts = controller.scope_counts(ready_state=ready_state)
    n_sel, n_ready, n_all = len(selected_ids), counts["ready"], counts["total"]
    options = {
        "selected": f"Selected · {n_sel}",
        "ready": f"{ready_label} · {n_ready}",
        "all": f"All · {n_all}",
    }
    initial = "selected" if n_sel else "ready"
    return ui.toggle(options, value=initial).props("no-caps").classes("colophon-seg")
