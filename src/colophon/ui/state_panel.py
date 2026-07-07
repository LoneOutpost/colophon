"""The detail panel's read-only State tab: a per-book diagnostics view of the
pipeline phases, identification scoring, classification signals, and findings.

`phase_rows` is the pure data shape (unit-tested); `render` builds the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from nicegui import ui

from colophon.core.guidance import FixAction, finding_guidance, review_guidance
from colophon.core.models import BookState, BookUnit, FindingCode, Phase, PhaseState
from colophon.core.phases import state_of
from colophon.core.provenance import provenance_label, provenance_tooltip
from colophon.core.review import review_reasons
from colophon.core.state_labels import phase_state_description, state_description

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttentionActions:
    """Behaviors the At-a-Glance Attention section triggers, supplied by the workspace
    (which owns the dialogs, tab control, selection, and navigation)."""

    acquire: Callable[[], None]
    reprobe: Callable[[], None]
    organize: Callable[[], None]
    files: Callable[[], None]
    matches: Callable[[], None]
    acknowledge: Callable[[FindingCode], None]

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
    Phase.SEARCH: "radar",
    Phase.CATEGORIZE: "category",
    Phase.IDENTIFY: "fingerprint",
    Phase.MATCH: "join_inner",
    Phase.TAG: "sell",
    Phase.ORGANIZE: "drive_file_move",
    Phase.ENCODE: "equalizer",
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


def _render_review_reasons(book: BookUnit) -> None:
    """Why this book reads uncertain — the book-level analogue of a node's kind_evidence. Shown only
    when there are reasons and the book isn't already source-verified/done."""
    if book.state in (BookState.READY, BookState.ORGANIZED, BookState.ENCODED):
        return
    reasons = review_reasons(book)
    if not reasons:
        return
    ui.label("Needs review because").classes("colophon-seccap")
    with ui.column().classes("w-full q-gutter-xs"):
        for reason in reasons:
            with ui.row().classes("items-start no-wrap q-gutter-xs"):
                ui.icon("error_outline", size="16px", color="warning").classes("q-mt-xs")
                ui.label(reason).classes("col text-caption")


def _render_identification(book: BookUnit) -> None:
    """The live local-identification evidence: what we think each identity field is, and where that
    value came from (its provenance tier). This is what the identity confidence is rolled up from."""
    rows: list[tuple[str, str, str | None]] = [
        ("Title", book.title or "—", book.provenance.get("title")),
        ("Author", ", ".join(book.authors) or "—", book.provenance.get("authors")),
        ("Series", "; ".join(s.name for s in book.series) or "—", book.provenance.get("series")),
    ]
    ui.label("Identification").classes("colophon-seccap")
    with ui.column().classes("w-full q-gutter-xs"):
        for field, value, prov in rows:
            with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                ui.label(field).classes("colophon-muted text-caption").style("width: 3.5rem")
                ui.label(value).classes("col ellipsis")
                plabel = provenance_label(prov)
                if plabel:
                    badge = ui.badge(plabel).props("color=grey-7 outline")
                    ptip = provenance_tooltip(prov)
                    if ptip:
                        badge.tooltip(ptip)


_ACTION_META: dict[FixAction, tuple[str, str]] = {
    FixAction.ACQUIRE: ("Go to Acquire", "cloud_download"),
    FixAction.REPROBE: ("Re-probe", "timer"),
    FixAction.ORGANIZE: ("Open Persist", "save"),
    FixAction.FILES: ("Files", "folder"),
    FixAction.MATCHES: ("Find matches", "travel_explore"),
    FixAction.ACKNOWLEDGE: ("Acknowledge", "check"),
}


def render(controller, book: BookUnit, *, actions: AttentionActions) -> None:
    """Read-only At-a-Glance tab for one book: derived state + confidence header, the phase
    timeline (with reserved, disabled per-phase re-run buttons), the confidence-signal
    breakdown, classification signals, and the Attention section (findings + a suggested
    next action each)."""
    from colophon.ui.workspace import _STATE_BADGE, _confidence_color  # local: avoid import cycle

    logger.debug(f"rendering At a Glance tab for book {book.id}")

    def _action_button(action: FixAction, code: FindingCode | None = None) -> None:
        text, icon = _ACTION_META[action]
        handlers: dict[FixAction, Callable[[], None]] = {
            FixAction.ACQUIRE: actions.acquire,
            FixAction.REPROBE: actions.reprobe,
            FixAction.ORGANIZE: actions.organize,
            FixAction.FILES: actions.files,
            FixAction.MATCHES: actions.matches,
            FixAction.ACKNOWLEDGE: (lambda c=code: actions.acknowledge(c)) if code else (lambda: None),
        }
        ui.button(text, icon=icon, on_click=handlers[action]).props("flat dense no-caps")

    with ui.column().classes("w-full q-gutter-xs q-pa-sm"):
        with ui.row().classes("items-center q-gutter-sm"):
            label, color = _STATE_BADGE.get(book.state, (book.state.value, "grey-6"))
            ui.badge(label).props(f"color={color} outline").tooltip(state_description(book.state))
            # The two confidences are distinct and both shown here, labelled: identity is the
            # pre-match local-identification rollup from the graph; match is the post-match score.
            ui.badge(f"Identity {book.identity_confidence:.0f}").props(
                f"color={_confidence_color(book.identity_confidence)}"
            ).tooltip(
                "Local-identification confidence: how sure we are we've identified this book "
                "from your library's structure and file tags, before matching an online source."
            )
            ui.badge(f"Match {book.confidence:.0f}").props(
                f"color={_confidence_color(book.confidence)}"
            ).tooltip("Match confidence: how strongly a matched source agrees. 0 until matched.")
            ui.label(f"/ threshold {controller.review_threshold():.0f}").classes(
                "colophon-muted text-caption"
            )

        _render_review_reasons(book)
        if review_reasons(book) and book.state not in (
            BookState.READY, BookState.ORGANIZED, BookState.ENCODED
        ):
            with ui.row().classes("q-pl-lg q-gutter-xs"):
                ui.label(review_guidance().suggestion).classes("colophon-muted text-caption")
            with ui.row().classes("q-pl-lg q-gutter-xs q-mb-sm"):
                _action_button(FixAction.MATCHES)
        _render_identification(book)

        ui.label("Pipeline").classes("colophon-seccap")
        with ui.column().classes("w-full q-gutter-none"):
            for row in phase_rows(book):
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm q-py-xs").style(
                    "border-bottom: 1px solid var(--colophon-hairline, rgba(0,0,0,0.08))"
                ):
                    ui.icon("circle", color=row.color, size="10px")
                    ui.icon(row.icon, size="18px").classes("colophon-muted")
                    ui.label(row.label).classes("col")
                    ui.badge(row.state.value).props(f"color={row.color} outline").tooltip(
                        phase_state_description(row.state)
                    )
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

        ui.label("Structure").classes("colophon-seccap")
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
            ui.label("Attention").classes("colophon-seccap")
            for f in findings:
                fc = {"error": "negative", "warn": "warning", "info": "info"}.get(
                    f.severity.value, "grey-6"
                )
                g = finding_guidance(f.code)
                with ui.column().classes("w-full q-gutter-none q-mb-sm"):
                    with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                        ui.icon("flag", color=fc, size="16px")
                        ui.label(f.detail).classes("col text-caption")
                    ui.label(g.suggestion).classes("colophon-muted text-caption q-pl-lg")
                    with ui.row().classes("q-pl-lg q-gutter-xs"):
                        for action in g.actions:
                            _action_button(action, f.code)
