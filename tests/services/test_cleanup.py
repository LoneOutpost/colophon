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


def test_broader_scan_path_keeps_book(tmp_path):
    downloads = tmp_path / "downloads"
    inner = downloads / "inner-scan" / "Book"
    inner.mkdir(parents=True)
    report = find_cleanup_candidates([_book(inner)], [downloads])
    assert report.removed_from_disk == []
    assert report.outside_scan_paths == []


def test_organized_book_is_never_a_candidate(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    gone_under = scan / "Gone"
    gone_outside = tmp_path / "away" / "Gone"
    out = tmp_path / "lib" / "x.m4b"
    report = find_cleanup_candidates(
        [_book(gone_under, output=out), _book(gone_outside, output=out)], [scan]
    )
    assert report.removed_from_disk == []
    assert report.outside_scan_paths == []


def test_unreachable_root_excludes_removed_from_disk(tmp_path):
    missing_root = tmp_path / "unmounted"
    book_folder = missing_root / "Book"
    report = find_cleanup_candidates([_book(book_folder)], [missing_root])
    assert report.removed_from_disk == []
    assert report.outside_scan_paths == []


def test_empty_scan_paths_puts_everything_outside(tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    b = tmp_path / "B"
    report = find_cleanup_candidates([_book(a), _book(b)], [])
    assert {c.source_folder for c in report.outside_scan_paths} == {a, b}
    assert report.removed_from_disk == []


def test_title_falls_back_to_folder_name(tmp_path):
    folder = tmp_path / "away" / "Some Book"
    c = find_cleanup_candidates([_book(folder, title=None)], [tmp_path / "scan"])
    assert c.outside_scan_paths[0].title == "Some Book"
