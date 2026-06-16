"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.dashboard import render_dashboard
from colophon.ui.settings import render_settings
from colophon.ui.triage import render_triage


def create_app(controller: AppController) -> None:
    @ui.page("/")
    def index() -> None:
        render_dashboard(controller)

    @ui.page("/triage")
    def triage() -> None:
        render_triage(controller)

    @ui.page("/settings")
    def settings() -> None:
        render_settings(controller)
