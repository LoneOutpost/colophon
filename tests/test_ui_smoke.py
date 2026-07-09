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


def test_render_manage_accepts_kind_and_filter_params():
    import inspect as _pyinspect

    from colophon.ui.manage import _valid_kind, render_manage

    sig = _pyinspect.signature(render_manage)
    assert "initial_kind" in sig.parameters
    assert "initial_filter" in sig.parameters
    assert _valid_kind("series") == "series"
    assert _valid_kind("bogus") == "author"


def test_skeleton_helpers_exist():
    from colophon.ui import skeleton
    assert callable(skeleton.skeleton_rows)
    import inspect
    params = inspect.signature(skeleton.skeleton_rows).parameters
    assert "count" in params and "height" in params


def test_repaint_is_defined_in_workspace_source():
    import inspect

    import colophon.ui.workspace as ws
    src = inspect.getsource(ws.render_workspace)
    assert "def repaint(" in src
    assert "repaint(nav=True, middle=True, status=True)" in src  # _refresh_all routes through it


def test_field_save_repaints_nav():
    # The field-save handler must include the navigator in its blast radius, so
    # clearing a shared entity (e.g. series) can't leave a ghost in "By Series".
    import inspect
    import re

    import colophon.ui.workspace as ws
    src = inspect.getsource(ws.render_workspace)
    m = re.search(r"def _save\(b=book\)[^:]*:.*?(?=\n\n? {16}def |\n\n? {16}async def )", src, re.S)
    assert m, "could not locate _save handler"
    body = m.group(0)
    assert "repaint(" in body and "nav=True" in body


def test_cold_build_paints_skeleton_and_warms_off_thread():
    import inspect

    import colophon.ui.workspace as ws
    src = inspect.getsource(ws.render_workspace)
    assert "async def _warm_tree" in src
    assert "asyncio.to_thread(controller.library_tree)" in src   # off-loop derive
    assert "def _ensure_warm" in src
    assert "library_tree_warm()" in src                          # sync fast path guard
    assert "skeleton_rows(" in src                               # skeletons on cold path


def test_warmer_uses_background_task_not_parentless_timer():
    # A ui.timer created inside an event-handler-triggered repaint has an empty
    # slot_stack, so the Timer element gets no parent and never fires — wedging the
    # warm flag so panes stick on the skeleton until a full page reload. The warmer
    # must be scheduled via background_tasks.create, which runs regardless of slot
    # context. See systematic-debugging root cause for the library async repaint work.
    import inspect
    import re

    import colophon.ui.workspace as ws
    src = inspect.getsource(ws.render_workspace)
    m = re.search(r" {4}def _ensure_warm\(\)[^:]*:.*?(?=\n {4}(?:async )?def )", src, re.S)
    assert m, "could not locate _ensure_warm"
    body = m.group(0)
    assert "background_tasks.create" in body, "_ensure_warm must schedule via background_tasks.create"
    assert "ui.timer(" not in body, "_ensure_warm must not schedule the warmer with a parentless ui.timer"


def test_index_applies_theme_before_awaiting_client():
    # The theme (incl. the .body--dark class) must ship in the initial HTML, before
    # the async index page awaits the client. Otherwise the Library page flashes the
    # light (warm/orangish) theme until dark-mode + _CSS land post-connect (FOUC).
    import inspect
    import re

    import colophon.ui as ui_pkg
    src = inspect.getsource(ui_pkg.create_app)
    m = re.search(r"async def index\(.*?(?=\n    @ui\.page|\n    @app\.get|\Z)", src, re.S)
    assert m, "could not locate index page function"
    body = m.group(0)
    assert "apply_theme()" in body and "setup_dark_mode()" in body
    assert body.index("apply_theme()") < body.index("client.connected()")
    assert body.index("setup_dark_mode()") < body.index("client.connected()")


def test_render_workspace_does_not_self_apply_theme():
    # Theme is now applied by the page handler before the await, not inside the
    # deferred render — so render_workspace must not call them itself.
    import inspect

    import colophon.ui.workspace as ws
    src = inspect.getsource(ws.render_workspace)
    assert "apply_theme()" not in src
    assert "setup_dark_mode()" not in src


def test_scope_selector_accepts_ready_tier_params():
    import inspect
    from colophon.ui.scope import scope_selector
    params = inspect.signature(scope_selector).parameters
    assert "ready_label" in params
    assert "ready_state" in params


def test_weak_id_trust_tiers_are_the_three_weak_provenances():
    from colophon.core.triage import WEAK_ID_TRUST_TIERS
    assert set(WEAK_ID_TRUST_TIERS) == {"directory", "filename", "graphing"}


def test_match_dialog_has_review_weak_link_and_workspace_wires_it():
    import inspect
    import colophon.ui.dialogs as dlg
    import colophon.ui.workspace as ws
    dsrc = inspect.getsource(dlg.match_dialog)
    assert "on_review_weak" in inspect.signature(dlg.match_dialog).parameters
    assert "Review in Library" in dsrc
    wsrc = inspect.getsource(ws.render_workspace)
    assert "def _review_weak_identity" in wsrc
    assert "WEAK_ID_TRUST_TIERS" in wsrc
    assert "on_review_weak=_review_weak_identity" in wsrc


def test_match_dialog_scopes_to_identified():
    import inspect
    import colophon.ui.dialogs as dlg
    src = inspect.getsource(dlg.match_dialog)
    assert "ready_state=BookState.IDENTIFIED" in src
    assert 'ready_label="Identified"' in src
    assert "ready to match against sources" in src  # the caption
