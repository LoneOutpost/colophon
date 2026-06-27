import pytest

from colophon.services.foster import foster_work


def test_foster_work_moves_files_into_named_subdir(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"")
    (tmp_path / "02.mp3").write_bytes(b"")
    dests = foster_work([tmp_path / "01.mp3", tmp_path / "02.mp3"], tmp_path, "The Way of Kings")
    assert (tmp_path / "The Way of Kings" / "01.mp3").is_file()
    assert (tmp_path / "The Way of Kings" / "02.mp3").is_file()
    assert dests == [tmp_path / "The Way of Kings" / "01.mp3",
                     tmp_path / "The Way of Kings" / "02.mp3"]


def test_foster_work_refuses_existing_dir(tmp_path):
    (tmp_path / "Legion").mkdir()
    (tmp_path / "a.mp3").write_bytes(b"")
    with pytest.raises(FileExistsError):
        foster_work([tmp_path / "a.mp3"], tmp_path, "Legion")


def test_unfoster_work_moves_files_back_and_removes_subdir(tmp_path):
    from colophon.services.foster import unfoster_work
    sub = tmp_path / "The Way of Kings"
    sub.mkdir()
    (sub / "01.mp3").write_bytes(b"")
    (sub / "02.mp3").write_bytes(b"")
    restored = unfoster_work(sub, tmp_path)
    assert (tmp_path / "01.mp3").is_file()
    assert (tmp_path / "02.mp3").is_file()
    assert restored == [tmp_path / "01.mp3", tmp_path / "02.mp3"]
    assert not sub.exists()  # emptied subdir removed


def test_unfoster_work_skips_name_collision(tmp_path):
    from colophon.services.foster import unfoster_work
    (tmp_path / "01.mp3").write_bytes(b"keep")  # already in parent
    sub = tmp_path / "Book"
    sub.mkdir()
    (sub / "01.mp3").write_bytes(b"new")
    restored = unfoster_work(sub, tmp_path)
    assert restored == []                       # collision left in place
    assert (tmp_path / "01.mp3").read_bytes() == b"keep"
    assert (sub / "01.mp3").is_file()           # not moved
    assert sub.exists()                         # not emptied, so not removed
