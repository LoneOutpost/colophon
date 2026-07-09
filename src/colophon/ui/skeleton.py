"""Calm loading placeholders for the Library panes.

Tonal, hairline-bordered rows sized to the real content so populating causes no
layout shift. No shimmer or spin — the visual system favours quiet loading states.
"""

from __future__ import annotations

from nicegui import ui


def skeleton_rows(count: int, height: str = "2.5rem") -> None:
    """Render `count` placeholder rows into the current container/slot."""
    for _ in range(count):
        with ui.element("div").classes(
            "w-full colophon-skeleton-row"
        ).style(f"height: {height}"):
            pass
