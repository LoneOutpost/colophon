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

    import colophon.ui.workspace as ws

    assert hasattr(ws, "render_workspace")

    import colophon.ui.acquire as aq

    assert hasattr(aq, "render_acquire")
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


def test_state_panel_render_is_callable():
    from colophon.ui import state_panel

    assert callable(state_panel.render)
    from pathlib import Path

    from colophon.core.models import BookUnit

    rows = state_panel.phase_rows(BookUnit.new(source_folder=Path("/x")))
    assert len(rows) == 7


def test_fosterable_plan_drives_attention_pane(tmp_path):
    from colophon.controller import AppController
    from colophon.core.models import BookUnit, DetectedWork
    from tests.test_controller import _ctx

    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Sarah Graves"
    author.mkdir(parents=True)
    (author / "Dead Cat Bounce.mp3").write_bytes(b"")
    (author / "A Face at the Window.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    book = ctx.books.get(BookUnit.id_for(author))
    book.detected_works = [
        DetectedWork(label="Dead Cat Bounce", files=[author / "Dead Cat Bounce.mp3"]),
        DetectedWork(label="A Face at the Window", files=[author / "A Face at the Window.mp3"]),
    ]
    ctx.books.upsert(book)
    plan = ctrl.fosterable_plan(book)
    assert plan is not None and len(plan.works) == 2
