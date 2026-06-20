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


def test_derive_book_fields_author_from_parent_folder(tmp_path):
    from colophon.services.foster import derive_book_fields
    dest = tmp_path / "Shiloh Walker" / "Burning Up" / "Burning Up.mp3"
    author, title = derive_book_fields(dest, None)
    assert author == "Shiloh Walker"
    assert title == "Burning Up"


def test_derive_book_fields_normalizes_title(tmp_path):
    from colophon.services.foster import derive_book_fields
    dest = tmp_path / "Author" / "burning_up" / "burning_up.mp3"
    _author, title = derive_book_fields(dest, None)
    assert title == "Burning Up"


def test_derive_book_fields_author_override(tmp_path):
    from colophon.services.foster import derive_book_fields
    dest = tmp_path / "Folder" / "Book" / "Book.mp3"
    author, _title = derive_book_fields(dest, "Pen Name")
    assert author == "Pen Name"
