from pathlib import Path

from colophon.core.models import BookUnit, SourceFile
from colophon.services.ingest import _files_changed


def _sf(p: Path) -> SourceFile:
    st = p.stat()
    return SourceFile(path=p, size=st.st_size, mtime_ns=st.st_mtime_ns, duration_seconds=1.0, ext="mp3")


def _book(paths: list[Path]) -> BookUnit:
    b = BookUnit.new(source_folder=paths[0].parent)
    b.source_files = [_sf(p) for p in paths]
    return b


def test_unchanged_files_are_not_changed(tmp_path):
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    book = _book([a])
    assert _files_changed(book, [a]) is False


def test_bumped_mtime_is_changed(tmp_path):
    import os
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    book = _book([a])
    os.utime(a, ns=(book.source_files[0].mtime_ns + 1_000_000_000,) * 2)
    assert _files_changed(book, [a]) is True


def test_changed_size_is_changed(tmp_path):
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    book = _book([a])
    a.write_bytes(b"xxxxx")
    assert _files_changed(book, [a]) is True


def test_added_file_is_changed(tmp_path):
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    b = tmp_path / "02.mp3"
    b.write_bytes(b"y")
    book = _book([a])
    assert _files_changed(book, [a, b]) is True


def test_removed_file_is_changed(tmp_path):
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    b = tmp_path / "02.mp3"
    b.write_bytes(b"y")
    book = _book([a, b])
    assert _files_changed(book, [a]) is True


def test_unstatable_path_is_changed(tmp_path):
    a = tmp_path / "01.mp3"
    a.write_bytes(b"x")
    book = _book([a])
    a.unlink()
    assert _files_changed(book, [a]) is True
