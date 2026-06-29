"""Stats page: a calm read-only breakdown of the library."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController
from colophon.core.stats import library_stats, top_entries
from colophon.ui.chrome import page_body, page_header, page_section

# Kinds shown as "top" lists, paired with their display label.
_TOP_KINDS = [
    ("author", "Top authors"),
    ("narrator", "Top narrators"),
    ("series", "Top series"),
    ("genre", "Top genres"),
]


def _fmt_hm(duration_ms: int) -> str:
    """Total listening time as 'Hh Mm' (or 'Mm' under an hour, '0m' when empty)."""
    minutes = round(duration_ms / 60000)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _stat_card(label: str, value: str) -> None:
    with ui.card().classes("col q-pa-md"):
        ui.label(value).classes("text-h5 text-weight-medium")
        ui.label(label).classes("text-caption colophon-muted")


def render_stats(controller: AppController) -> None:
    with page_header(controller, "stats", icon="insights", label="Stats"):
        pass

    books = controller.books_all()
    stats = library_stats(books)

    with page_body("full"):
        if not books:
            with ui.card().classes("w-full q-pa-md"):
                ui.label("No books yet").classes("text-subtitle1")
                ui.label("Scan a library to see statistics here.").classes(
                    "text-caption colophon-muted"
                )
            return

        with ui.row().classes("w-full q-gutter-md no-wrap"):
            _stat_card("Books", str(stats.total_books))
            _stat_card("Listening time", _fmt_hm(stats.total_duration_ms))
            _stat_card("Library size", _fmt_size(stats.total_bytes))

        with page_section("By state"):
            peak = max((c for _, c in stats.by_state), default=1)
            for state, count in stats.by_state:
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    ui.label(state.value.replace("_", " ").capitalize()).classes(
                        "text-body2"
                    ).style("min-width: 9rem")
                    with ui.element("div").classes("col rounded").style(
                        "height: 6px; background: var(--colophon-line)"
                    ):
                        ui.element("div").classes("rounded").style(
                            f"height: 6px; width: {count / peak * 100:.1f}%; "
                            "background: var(--colophon-accent)"
                        )
                    ui.label(str(count)).classes("text-body2 colophon-muted").style(
                        "min-width: 2.5rem; text-align: right"
                    )

        with ui.row().classes("w-full q-gutter-md items-stretch"):
            for kind, label in _TOP_KINDS:
                entries = top_entries(books, kind, 8)
                with ui.column().classes("col"), page_section(label):
                    if not entries:
                        ui.label("None yet").classes("text-caption colophon-muted")
                    for entry in entries:
                        with ui.row().classes(
                            "items-center w-full no-wrap cursor-pointer"
                        ).on("click", lambda e=entry: ui.navigate.to(f"/?filter={e.name}")):
                            ui.label(entry.name).classes("col ellipsis text-body2")
                            ui.badge(str(entry.count)).props("outline").classes(
                                "colophon-chip"
                            )
