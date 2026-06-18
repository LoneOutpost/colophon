"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.acquire import render_acquire
from colophon.ui.settings import render_settings
from colophon.ui.workspace import render_workspace


def create_app(controller: AppController) -> None:
    @ui.page("/")
    def index() -> None:
        render_workspace(controller)

    @ui.page("/settings")
    def settings() -> None:
        render_settings(controller)

    @ui.page("/acquire")
    def acquire() -> None:
        render_acquire(controller)
