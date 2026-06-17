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

    import colophon.ui.workspace as ws

    assert hasattr(ws, "render_workspace")
    ctx.close()


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
