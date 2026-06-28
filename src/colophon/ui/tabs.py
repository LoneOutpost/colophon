"""Shared top navigation tab strip, rendered inside each page header."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController

# (label, route, key)
_BASE_TABS = [
    ("Library", "/", "library"),
    ("Manage", "/manage", "manage"),
    ("Stats", "/stats", "stats"),
    ("Graph", "/graph", "graph"),
    ("Settings", "/settings", "settings"),
]


def app_tabs(controller: AppController, active: str) -> None:
    """Render the navigation tabs, highlighting `active`. Acquire appears only when
    Real-Debrid is configured."""
    tabs = list(_BASE_TABS)
    if controller.rd_configured():
        tabs.append(("Acquire", "/acquire", "acquire"))
    with ui.row().classes("items-center q-gutter-xs q-ml-md no-wrap"):
        for label, route, key in tabs:
            btn = ui.button(label, on_click=lambda r=route: ui.navigate.to(r)).props("flat no-caps")
            if key == active:
                btn.props("color=primary").classes("text-weight-medium")
            else:
                btn.props("color=grey-7")
