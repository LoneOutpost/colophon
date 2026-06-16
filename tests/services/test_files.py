from pathlib import Path

import pytest

from colophon.core.models import BookUnit, SourceFile
from colophon.services import files


def _sf(path: Path, secs: float = 60.0) -> SourceFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return SourceFile(path=path, size=1, duration_seconds=secs, ext=path.suffix.lstrip("."))


def _book(tmp_path) -> BookUnit:
    b = BookUnit.new(source_folder=tmp_path / "book")
    b.source_files = [
        _sf(tmp_path / "book" / "01.mp3"),
        _sf(tmp_path / "book" / "02.mp3"),
        _sf(tmp_path / "book" / "03.mp3"),
    ]
    return b


def test_reorder_sets_new_order(tmp_path):
    b = _book(tmp_path)
    new = [b.source_files[2].path, b.source_files[0].path, b.source_files[1].path]
    files.reorder(b, new)
    assert [sf.path.name for sf in b.source_files] == ["03.mp3", "01.mp3", "02.mp3"]


def test_reorder_rejects_non_permutation(tmp_path):
    b = _book(tmp_path)
    with pytest.raises(ValueError, match="permutation"):
        files.reorder(b, [b.source_files[0].path])  # missing files


def test_exclude_drops_file_without_deleting_from_disk(tmp_path):
    b = _book(tmp_path)
    victim = b.source_files[1].path
    files.exclude(b, victim)
    assert [sf.path.name for sf in b.source_files] == ["01.mp3", "03.mp3"]
    assert victim.exists()  # not deleted from disk


def test_rename_moves_on_disk_and_updates_source_file(tmp_path):
    b = _book(tmp_path)
    old = b.source_files[0].path
    new = files.rename(b, old, "00 - Intro.mp3")
    assert new.name == "00 - Intro.mp3"
    assert new.exists() and not old.exists()
    assert b.source_files[0].path == new


def test_rename_collision_raises(tmp_path):
    b = _book(tmp_path)
    with pytest.raises(FileExistsError):
        files.rename(b, b.source_files[0].path, "02.mp3")  # 02.mp3 already exists
