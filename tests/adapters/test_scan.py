from pathlib import Path

from colophon.adapters.scan import group_book_units


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_each_folder_with_audio_is_one_unit(tmp_path: Path):
    _touch(tmp_path / "Dune" / "01.mp3")
    _touch(tmp_path / "Dune" / "02.mp3")
    _touch(tmp_path / "Mistborn" / "book.m4b")
    _touch(tmp_path / "Mistborn" / "cover.jpg")  # non-audio ignored

    units = group_book_units(tmp_path)
    by_name = {u.folder.name: u for u in units}
    assert set(by_name) == {"Dune", "Mistborn"}
    assert [p.name for p in by_name["Dune"].files] == ["01.mp3", "02.mp3"]
    assert [p.name for p in by_name["Mistborn"].files] == ["book.m4b"]


def test_folders_without_audio_are_skipped(tmp_path: Path):
    _touch(tmp_path / "art" / "cover.jpg")
    assert group_book_units(tmp_path) == []


def test_units_sorted_by_folder_name(tmp_path: Path):
    _touch(tmp_path / "Zoo" / "a.mp3")
    _touch(tmp_path / "Apple" / "a.mp3")
    units = group_book_units(tmp_path)
    assert [u.folder.name for u in units] == ["Apple", "Zoo"]


def test_files_are_natural_sorted(tmp_path: Path):
    # Create in scrambled order to ensure the sort does the work.
    _touch(tmp_path / "Book" / "10.mp3")
    _touch(tmp_path / "Book" / "2.mp3")
    _touch(tmp_path / "Book" / "1.mp3")
    units = group_book_units(tmp_path)
    (unit,) = units
    assert [p.name for p in unit.files] == ["1.mp3", "2.mp3", "10.mp3"]
