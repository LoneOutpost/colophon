"""The detail panel's read-only State tab: a per-book diagnostics view of the
pipeline phases, identification scoring, classification signals, and findings.

`phase_rows` is the pure data shape (unit-tested); `render` builds the UI.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import NamedTuple

from nicegui import ui

from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import state_of

logger = logging.getLogger(__name__)

_PHASE_LABELS: dict[Phase, str] = {
    Phase.SEARCH: "Search",
    Phase.CATEGORIZE: "Categorize",
    Phase.IDENTIFY: "Identify",
    Phase.MATCH: "Match",
    Phase.TAG: "Tag",
    Phase.ORGANIZE: "Organize",
    Phase.ENCODE: "Encode",
}

# Static, non-status icons reused by the nav phase mode and the timeline.
_PHASE_ICONS: dict[Phase, str] = {
    Phase.SEARCH: "search",
    Phase.CATEGORIZE: "category",
    Phase.IDENTIFY: "fingerprint",
    Phase.MATCH: "compare_arrows",
    Phase.TAG: "sell",
    Phase.ORGANIZE: "folder_open",
    Phase.ENCODE: "graphic_eq",
}

_PHASE_STATE_COLOR: dict[PhaseState, str] = {
    PhaseState.PENDING: "grey-5",
    PhaseState.FRESH: "positive",
    PhaseState.STALE: "warning",
    PhaseState.RUNNING: "info",
    PhaseState.FAILED: "negative",
}


class PhaseRow(NamedTuple):
    phase: Phase
    label: str
    icon: str
    state: PhaseState
    color: str
    updated_at: datetime | None
    detail: str | None


def phase_rows(book: BookUnit) -> list[PhaseRow]:
    """One row per phase, in pipeline order, describing its current state for display."""
    rows: list[PhaseRow] = []
    for phase in Phase:
        record = book.phases.get(phase)
        st = state_of(book, phase)
        rows.append(PhaseRow(
            phase=phase,
            label=_PHASE_LABELS[phase],
            icon=_PHASE_ICONS[phase],
            state=st,
            color=_PHASE_STATE_COLOR[st],
            updated_at=record.updated_at if record is not None else None,
            detail=record.detail if record is not None else None,
        ))
    return rows


def render(controller, book: BookUnit) -> None:
    """Read-only State tab for one book: derived state + confidence header, the phase
    timeline (with reserved, disabled per-phase re-run buttons), the confidence-signal
    breakdown, classification signals, and active findings."""
    from colophon.ui.workspace import _STATE_BADGE, _confidence_color  # local: avoid import cycle

    logger.debug(f"rendering State tab for book {book.id}")

    with ui.column().classes("w-full q-gutter-sm q-pa-sm"):
        with ui.row().classes("items-center q-gutter-sm"):
            label, color = _STATE_BADGE.get(book.state, (book.state.value, "grey-6"))
            ui.badge(label).props(f"color={color} outline")
            ui.badge(f"{book.confidence:.0f}").props(f"color={_confidence_color(book.confidence)}")
            ui.label(f"/ threshold {controller.review_threshold():.0f}").classes(
                "colophon-muted text-caption"
            )

        ui.label("Pipeline").classes("colophon-seccap")
        with ui.column().classes("w-full q-gutter-none"):
            for row in phase_rows(book):
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm q-py-xs").style(
                    "border-bottom: 1px solid var(--colophon-hairline, rgba(0,0,0,0.08))"
                ):
                    ui.icon("circle", color=row.color, size="10px")
                    ui.icon(row.icon, size="18px").classes("colophon-muted")
                    ui.label(row.label).classes("col")
                    ui.badge(row.state.value).props(f"color={row.color} outline")
                    if row.updated_at is not None:
                        ui.label(row.updated_at.strftime("%Y-%m-%d %H:%M")).classes(
                            "colophon-muted text-caption"
                        )
                    btn = ui.button(icon="refresh").props("flat dense round color=grey-6")
                    btn.set_enabled(False)
                    btn.tooltip("Re-run — coming soon")
                if row.detail:
                    ui.label(row.detail).classes("colophon-muted text-caption q-pl-lg")

        ui.label("Confidence").classes("colophon-seccap")
        if book.confidence_signals:
            with ui.column().classes("w-full q-gutter-xs"):
                for sig in book.confidence_signals:
                    c = "positive" if sig.points >= 0 else "negative"
                    with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                        ui.badge(f"{sig.points:+d}").props(f"color={c} outline")
                        ui.label(sig.name.replace("_", " ")).classes("col")
                        if sig.detail:
                            ui.label(sig.detail).classes("colophon-muted text-caption")
        else:
            ui.label("No confidence signals yet.").classes("colophon-muted text-caption")

        ui.label("Classification").classes("colophon-seccap")
        with ui.column().classes("w-full q-gutter-xs"):
            ui.label(
                f"Folder: {book.folder_kind.value} · Content: {book.content_kind.value} "
                f"· Structure confidence: {book.classification_confidence:.0f}"
            ).classes("text-caption")
            if book.detected_works:
                ui.label(f"Detected works ({len(book.detected_works)}):").classes("text-caption")
                for w in book.detected_works:
                    ui.label(f"• {w.label}").classes("colophon-muted text-caption q-pl-sm")
            for sig in book.classification_signals:
                sign = "+" if sig.points >= 0 else ""
                ui.label(
                    f"• {sig.name.replace('_', ' ')} ({sign}{sig.points})"
                ).classes("colophon-muted text-caption q-pl-sm")

        findings = controller._active_findings(book)
        if findings:
            ui.label("Findings").classes("colophon-seccap")
            for f in findings:
                fc = {"error": "negative", "warn": "warning", "info": "info"}.get(
                    f.severity.value, "grey-6"
                )
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    ui.icon("flag", color=fc, size="16px")
                    ui.label(f.detail).classes("col text-caption")
