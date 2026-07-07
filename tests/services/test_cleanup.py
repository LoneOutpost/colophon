"""Unit tests for find_cleanup_candidates — the pure classifier behind the Utilities
clean-up action."""

from colophon.core.models import BookUnit
from colophon.services.cleanup import find_cleanup_candidates


def _book(folder, *, title=None, output=None):
    b = BookUnit.new(source_folder=folder)
    b.title = title
    b.output_path = output
    return b


def test_splits_into_disjoint_categories(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    present = scan / "Kept"
    present.mkdir()
    gone = scan / "Gone"  # under scan path but never created on disk
    outside = tmp_path / "elsewhere" / "Orphan"  # not under scan path

    report = find_cleanup_candidates(
        [_book(present), _book(gone), _book(outside)], [scan]
    )

    assert [c.source_folder for c in report.removed_from_disk] == [gone]
    assert [c.source_folder for c in report.outside_scan_paths] == [outside]
    all_folders = {c.source_folder for c in report.removed_from_disk} | {
        c.source_folder for c in report.outside_scan_paths
    }
    assert present not in all_folders
    assert not (
        {c.source_folder for c in report.removed_from_disk}
        & {c.source_folder for c in report.outside_scan_paths}
    )
