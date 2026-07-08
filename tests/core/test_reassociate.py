from pathlib import Path

from colophon.core.models import BookUnit, SourceFile
from colophon.core.reassociate import is_missing, reassociate


def _book(folder: Path, files: list[tuple[str, int, float]]) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.source_files = [
        SourceFile(path=folder / name, size=size, duration_seconds=dur, ext=".mp3")
        for name, size, dur in files
    ]
    return b


def test_identical_sets_match_one_to_one(tmp_path):
    e = _book(tmp_path, [("a.mp3", 100, 60.0)])
    p = _book(tmp_path, [("a.mp3", 100, 60.0)])
    assert reassociate([p], [e]) == [(p, e)]


def test_file_added_still_matches(tmp_path):
    e = _book(tmp_path, [("a.mp3", 100, 60.0)])
    p = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0)])
    assert reassociate([p], [e]) == [(p, e)]


def test_file_removed_still_matches(tmp_path):
    e = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0)])
    p = _book(tmp_path, [("a.mp3", 100, 60.0)])
    assert reassociate([p], [e]) == [(p, e)]


def test_single_renamed_file_does_not_match(tmp_path):
    # name differs, size+duration hold; with a SINGLE renamed file there is no shared
    # (name,size,dur) tuple, so this is a genuine non-match.
    e = _book(tmp_path, [("01 - intro.mp3", 100, 60.0)])
    p = _book(tmp_path, [("intro.mp3", 100, 60.0)])
    assert reassociate([p], [e]) == [(p, None)]


def test_rename_with_another_shared_file_matches(tmp_path):
    e = _book(tmp_path, [("01.mp3", 100, 60.0), ("02.mp3", 200, 70.0)])
    p = _book(tmp_path, [("track01.mp3", 100, 60.0), ("02.mp3", 200, 70.0)])
    assert reassociate([p], [e]) == [(p, e)]  # shares 02.mp3


def test_split_dominant_leaf_inherits(tmp_path):
    old = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0), ("c.mp3", 300, 80.0)])
    big = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0)])  # 2 shared
    small = _book(tmp_path, [("c.mp3", 300, 80.0)])  # 1 shared
    pairs = reassociate([big, small], [old])  # returned in projected input order
    assert pairs == [(big, old), (small, None)]


def test_merge_highest_overlap_old_wins(tmp_path):
    big = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0)])
    small = _book(tmp_path, [("c.mp3", 300, 80.0)])
    new = _book(tmp_path, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0), ("c.mp3", 300, 80.0)])
    pairs = reassociate([new], [big, small])
    assert pairs == [(new, big)]  # big shares 2, small shares 1


def test_unrelated_never_matches(tmp_path):
    e = _book(tmp_path, [("a.mp3", 100, 60.0)])
    p = _book(tmp_path, [("z.mp3", 999, 12.0)])
    assert reassociate([p], [e]) == [(p, None)]


def test_existing_claimed_at_most_once(tmp_path):
    old = _book(tmp_path, [("a.mp3", 100, 60.0)])
    p1 = _book(tmp_path, [("a.mp3", 100, 60.0)])
    p2 = _book(tmp_path, [("a.mp3", 100, 60.0)])
    pairs = reassociate([p1, p2], [old])
    matched = [p for p, m in pairs if m is old]
    assert len(matched) == 1  # only one projected leaf can inherit old


def test_is_missing_folder_gone_root_accessible(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "gone")  # never created
    assert is_missing(b, root_accessible=True) is True


def test_is_missing_false_when_folder_exists(tmp_path):
    folder = tmp_path / "here"
    folder.mkdir()
    b = BookUnit.new(source_folder=folder)
    assert is_missing(b, root_accessible=True) is False


def test_is_missing_false_when_organized(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "gone")
    b.output_path = tmp_path / "library" / "out.m4b"
    assert is_missing(b, root_accessible=True) is False


def test_is_missing_false_when_root_inaccessible(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "gone")
    assert is_missing(b, root_accessible=False) is False  # unmount guard


def test_is_missing_when_own_files_gone_but_dir_kept_by_sibling(tmp_path):
    # Multi-book download directory: two books share one folder. The folder still
    # exists because a sibling's file remains, but THIS book's own audio was deleted.
    # A directory-existence check alone wrongly reports it present; the book is gone.
    shared = tmp_path / "TE_Audiobooks_C"
    shared.mkdir()
    (shared / "sibling.mp3").write_bytes(b"x")  # a sibling book keeps the folder alive
    b = _book(shared, [("Lightless.mp3", 166, 41587.0)])  # its own file was never created
    assert is_missing(b, root_accessible=True) is True


def test_is_missing_false_when_own_file_present_in_shared_dir(tmp_path):
    shared = tmp_path / "TE_Audiobooks_C"
    shared.mkdir()
    (shared / "Lightless.mp3").write_bytes(b"x")
    b = _book(shared, [("Lightless.mp3", 166, 41587.0)])
    assert is_missing(b, root_accessible=True) is False


def test_is_missing_false_when_any_own_file_survives(tmp_path):
    # Partial deletion is not "missing": a book with content left is still present.
    shared = tmp_path / "book"
    shared.mkdir()
    (shared / "a.mp3").write_bytes(b"x")  # b.mp3 deleted, a.mp3 survives
    b = _book(shared, [("a.mp3", 100, 60.0), ("b.mp3", 200, 70.0)])
    assert is_missing(b, root_accessible=True) is False
