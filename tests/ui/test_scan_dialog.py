import inspect

from colophon.services.ingest import ScanScope
from colophon.ui.dialogs import _DEPTH_TO_SCOPE, match_dialog, persist_dialog, scan_dialog


def test_depth_maps_to_scope():
    assert _DEPTH_TO_SCOPE["new_changed"] is ScanScope.UPDATE
    assert _DEPTH_TO_SCOPE["rebuild"] is ScanScope.REFRESH
    assert set(_DEPTH_TO_SCOPE) == {"new_changed", "rebuild"}


def test_stage_dialogs_are_coroutine_functions():
    # The header Scan/Match/Persist handlers await these. If any became a plain (non-async)
    # def, the button's handler would silently discard the returned coroutine and the dialog
    # would never open — a regression the UI smoke test can't catch (it renders pages, not
    # click handlers). This guards against reintroducing that bug.
    assert inspect.iscoroutinefunction(scan_dialog)
    assert inspect.iscoroutinefunction(match_dialog)
    assert inspect.iscoroutinefunction(persist_dialog)
