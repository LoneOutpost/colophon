from pathlib import Path

import pytest

from colophon.services.foster import FosterResult, foster_one


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_foster_one_moves_file_into_stem_named_subdir(tmp_path: Path):
    src = _touch(tmp_path / "Mistborn.mp3")
    dest = foster_one(src)
    assert dest == tmp_path / "Mistborn" / "Mistborn.mp3"
    assert dest.is_file()
    assert not src.exists()


def test_foster_one_preserves_extension_and_full_name(tmp_path: Path):
    src = _touch(tmp_path / "Legion.m4b")
    dest = foster_one(src)
    assert dest == tmp_path / "Legion" / "Legion.m4b"


def test_foster_one_raises_when_target_dir_exists(tmp_path: Path):
    src = _touch(tmp_path / "Warbreaker.mp3")
    (tmp_path / "Warbreaker").mkdir()  # collision
    with pytest.raises(FileExistsError):
        foster_one(src)
    assert src.exists()  # original untouched on failure


def test_foster_one_raises_when_path_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        foster_one(tmp_path / "ghost.mp3")


def test_foster_result_is_pydantic_with_expected_fields(tmp_path: Path):
    r = FosterResult(source=tmp_path / "a.mp3", destination=tmp_path / "a" / "a.mp3", ok=True)
    assert r.ok is True and r.error is None
    r2 = FosterResult(source=tmp_path / "b.mp3", ok=False, error="boom")
    assert r2.destination is None and r2.error == "boom"
