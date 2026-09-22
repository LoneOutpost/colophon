from pathlib import Path

import pytest

from colophon.core.models import BookUnit, EmbeddedTags, SourceFile
from colophon.services.files import move_on_disk, move_to_edge


def test_move_on_disk_relocates_and_creates_dest(tmp_path):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    f = src_dir / "01.mp3"
    f.write_bytes(b"audio")
    dest = tmp_path / "b" / "c"  # does not exist yet
    new = move_on_disk(f, dest)
    assert new == dest / "01.mp3"
    assert new.exists() and new.read_bytes() == b"audio"
    assert not f.exists()


def test_move_on_disk_renames_when_new_name_given(tmp_path):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    f = src_dir / "01.mp3"
    f.write_bytes(b"x")
    new = move_on_disk(f, src_dir, "renamed.mp3")  # same dir => in-place rename
    assert new == src_dir / "renamed.mp3"
    assert new.exists() and not f.exists()


def test_move_on_disk_raises_on_collision_without_overwriting(tmp_path):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    f = src_dir / "01.mp3"
    f.write_bytes(b"src")
    dest = tmp_path / "b"
    dest.mkdir()
    existing = dest / "01.mp3"
    existing.write_bytes(b"dest")
    with pytest.raises(FileExistsError):
        move_on_disk(f, dest)
    assert f.exists()                       # source untouched
    assert existing.read_bytes() == b"dest"  # destination not overwritten


def test_move_on_disk_rejects_empty_name(tmp_path):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    f = src_dir / "01.mp3"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        move_on_disk(f, src_dir, "   ")


def test_move_on_disk_rejects_path_separator_in_name(tmp_path):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    f = src_dir / "01.mp3"
    f.write_bytes(b"x")
    for bad in ["../escape.mp3", "sub/evil.mp3", ".."]:
        with pytest.raises(ValueError):
            move_on_disk(f, tmp_path / "dest", bad)
    assert f.exists()  # source never moved


def _book_with_files(names: list[str]) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/audio/x"))
    b.source_files = [SourceFile(path=Path(n), size=1, duration_seconds=1.0, ext="mp3",
                                 tags=EmbeddedTags()) for n in names]
    return b


def test_move_to_edge_sends_a_block_to_the_bottom_keeping_relative_order():
    b = _book_with_files(["a", "b", "c", "d", "e"])
    move_to_edge(b, [Path("b"), Path("d")], "bottom")
    assert [sf.path.name for sf in b.source_files] == ["a", "c", "e", "b", "d"]


def test_move_to_edge_sends_a_block_to_the_top_keeping_relative_order():
    b = _book_with_files(["a", "b", "c", "d", "e"])
    move_to_edge(b, [Path("d"), Path("b")], "top")
    # 'b' precedes 'd' in the book, so it precedes it at the top too — the CALLER's argument order
    # is not the ordering; the book's existing order is.
    assert [sf.path.name for sf in b.source_files] == ["b", "d", "a", "c", "e"]


def test_move_to_edge_is_a_noop_for_an_empty_selection():
    b = _book_with_files(["a", "b", "c"])
    move_to_edge(b, [], "top")
    assert [sf.path.name for sf in b.source_files] == ["a", "b", "c"]


def test_move_to_edge_ignores_a_path_the_book_does_not_own():
    b = _book_with_files(["a", "b", "c"])
    move_to_edge(b, [Path("b"), Path("zzz")], "top")
    assert [sf.path.name for sf in b.source_files] == ["b", "a", "c"]


def test_move_to_edge_rejects_an_unknown_edge():
    b = _book_with_files(["a", "b"])
    with pytest.raises(ValueError):
        move_to_edge(b, [Path("a")], "sideways")


def test_delete_directory_from_disk_removes_tree(tmp_path):
    from colophon.services.files import delete_directory_from_disk
    d = tmp_path / "book"
    (d / "sub").mkdir(parents=True)
    (d / "01.mp3").write_bytes(b"a")
    (d / "sub" / "02.mp3").write_bytes(b"b")
    assert delete_directory_from_disk(d) is True
    assert not d.exists()


def test_delete_directory_from_disk_false_on_missing(tmp_path):
    from colophon.services.files import delete_directory_from_disk
    assert delete_directory_from_disk(tmp_path / "nope") is False


def test_remove_if_empty_removes_empty_folder(tmp_path):
    from colophon.services.files import remove_if_empty
    d = tmp_path / "empty"
    d.mkdir()
    assert remove_if_empty(d) is True
    assert not d.exists()


def test_remove_if_empty_noop_when_not_empty(tmp_path):
    from colophon.services.files import remove_if_empty
    d = tmp_path / "full"
    d.mkdir()
    (d / "cover.jpg").write_bytes(b"x")
    assert remove_if_empty(d) is False
    assert d.exists()
