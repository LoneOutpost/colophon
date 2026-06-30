from colophon.services.ingest import ScanScope
from colophon.ui.dialogs import _DEPTH_TO_SCOPE


def test_depth_maps_to_scope():
    assert _DEPTH_TO_SCOPE["new_changed"] is ScanScope.UPDATE
    assert _DEPTH_TO_SCOPE["rebuild"] is ScanScope.REFRESH
    assert set(_DEPTH_TO_SCOPE) == {"new_changed", "rebuild"}
