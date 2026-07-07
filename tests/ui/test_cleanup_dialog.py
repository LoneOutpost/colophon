from pathlib import Path

from colophon.services.cleanup import CleanupCandidate, CleanupReport
from colophon.ui.manage import _selected_cleanup_ids


def _report():
    return CleanupReport(
        removed_from_disk=[
            CleanupCandidate("d1", "D1", Path("/scan/d1"), "removed_from_disk")
        ],
        outside_scan_paths=[
            CleanupCandidate("o1", "O1", Path("/away/o1"), "outside_scan_paths"),
            CleanupCandidate("o2", "O2", Path("/away/o2"), "outside_scan_paths"),
        ],
    )


def test_selects_only_checked_categories():
    r = _report()
    assert _selected_cleanup_ids(r, {"removed_from_disk"}) == ["d1"]
    assert _selected_cleanup_ids(r, {"outside_scan_paths"}) == ["o1", "o2"]
    assert set(_selected_cleanup_ids(r, {"removed_from_disk", "outside_scan_paths"})) == {
        "d1", "o1", "o2"
    }
    assert _selected_cleanup_ids(r, set()) == []
