from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def test_create_app_registers_pages_without_running(tmp_path):
    # building the app must register routes and not raise; it must NOT start a server
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"))
    from colophon.ui import create_app

    create_app(AppController(ctx))  # registers @ui.page routes

    from nicegui import Client

    routes = set(Client.page_routes.values())
    assert "/" in routes
    assert "/settings" in routes
    assert "/acquire" in routes
    assert "/graph" in routes

    import colophon.ui.workspace as ws

    assert hasattr(ws, "render_workspace")
    # Lazy scan-if-empty auto-scan: the once-per-process guard must exist and
    # start unset, and the controller surface the bootstrap depends on must be present.
    assert ws._auto_scan_attempted is False
    assert callable(AppController.scan_paths_missing_graph)
    assert callable(AppController.scan_preview_streamed)
    assert callable(AppController.apply_scan)

    import colophon.ui.acquire as aq

    assert hasattr(aq, "render_acquire")
    ctx.close()


def test_missing_remove_contract_exists():
    # The workspace's "Remove missing" control depends on these two surfaces:
    # BookUnit.missing (gates the badge/button) and controller.remove_missing.
    from pathlib import Path

    from colophon.controller import AppController
    from colophon.core.models import BookUnit

    assert hasattr(BookUnit.new(source_folder=Path("/x")), "missing")
    assert callable(AppController.remove_missing)


def test_main_is_importable():
    import colophon.__main__ as m

    assert hasattr(m, "main")


def test_fmt_duration_hours_and_minutes():
    from colophon.ui.workspace import _fmt_duration

    assert _fmt_duration(0) == "0m"
    assert _fmt_duration(47 * 60) == "47m"
    assert _fmt_duration(3725) == "1h 2m"   # 62.08 min -> 1h 2m
    assert _fmt_duration(3600) == "1h 0m"
    assert _fmt_duration(59 * 60 + 40) == "1h 0m"  # 59m40s rounds to 60 -> 1h 0m


def test_state_panel_render_is_callable():
    from colophon.ui import state_panel

    assert callable(state_panel.render)
    from pathlib import Path

    from colophon.core.models import BookUnit

    rows = state_panel.phase_rows(BookUnit.new(source_folder=Path("/x")))
    assert len(rows) == 7
