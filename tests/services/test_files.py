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


def test_delete_files_from_disk_removes_and_reports(tmp_path):
    from colophon.services.files import delete_files_from_disk

    a = tmp_path / "a.mp3"
    a.write_bytes(b"x")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"y")
    gone = tmp_path / "gone.mp3"  # already absent

    removed = delete_files_from_disk([a, b, gone])

    assert not a.exists() and not b.exists()
    # an already-absent path counts as removed (goal achieved) so the caller drops it from the book too
    assert set(removed) == {a, b, gone}


def test_folder_has_audio_true_for_direct_and_nested_audio(tmp_path):
    from colophon.services.files import folder_has_audio
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "01.mp3").write_bytes(b"")
    assert folder_has_audio(tmp_path)
    nested = tmp_path / "b" / "sub"
    nested.mkdir(parents=True)
    (nested / "part.opus").write_bytes(b"")   # .opus counts as audio
    assert folder_has_audio(tmp_path / "b")


def test_folder_has_audio_false_for_non_audio_only_or_missing(tmp_path):
    from colophon.services.files import folder_has_audio
    d = tmp_path / "covers"
    d.mkdir()
    (d / "cover.jpg").write_bytes(b"")
    (d / "book.nfo").write_bytes(b"")
    assert not folder_has_audio(d)
    assert not folder_has_audio(tmp_path / "does-not-exist")


def test_folder_has_audio_respects_ignoring(tmp_path):
    from colophon.services.files import folder_has_audio
    a = tmp_path / "01.mp3"
    b = tmp_path / "02.mp3"
    a.write_bytes(b"")
    b.write_bytes(b"")
    assert not folder_has_audio(tmp_path, ignoring=frozenset({a, b}))   # all audio ignored
    assert folder_has_audio(tmp_path, ignoring=frozenset({a}))          # b still remains
