import pytest

from colophon.services.files import move_on_disk


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
