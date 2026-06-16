from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def test_create_app_registers_pages_without_running(tmp_path):
    # building the app must register routes and not raise; it must NOT start a server
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"))
    from colophon.ui import create_app

    create_app(AppController(ctx))  # registers @ui.page routes

    from nicegui import Client

    assert "/" in Client.page_routes.values() or any(
        r in Client.page_routes.values() for r in ("/", "/triage", "/settings")
    )
    ctx.close()


def test_main_is_importable():
    import colophon.__main__ as m

    assert hasattr(m, "main")
