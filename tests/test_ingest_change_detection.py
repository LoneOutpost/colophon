import os
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, SourceFile
from colophon.core.phases import LOCAL
from colophon.services import ingest as ingest_mod
from colophon.services.ingest import ScanOptions, ScanScope, _files_changed


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


def _mp3(p: Path, artist="A"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    t = ID3()
    t.add(TPE1(encoding=3, text=[artist]))
    t.save(p)


def _ctrl(tmp_path):
    ingest = tmp_path / "audio"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
                                   scan_paths=[ingest]))
    return ctx, AppController(ctx), ingest


def _update_rescan(ctrl, monkeypatch):
    """Run an UPDATE-scope rescan; return the read_audio_metadata call paths it made."""
    calls = []
    real = ingest_mod.read_audio_metadata
    monkeypatch.setattr(ingest_mod, "read_audio_metadata", lambda p: (calls.append(p), real(p))[1])
    opts = ScanOptions(scope=ScanScope.UPDATE, phases=frozenset(LOCAL))
    ctrl.apply_scan(ctrl.scan_preview(options=opts))
    return calls


def test_no_change_rescan_reparses_nothing(tmp_path, monkeypatch):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    calls = _update_rescan(ctrl, monkeypatch)
    assert calls == []
    ctx.close()


def test_touched_file_reparses_its_book(tmp_path, monkeypatch):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    f = ingest / "Dune" / "01.mp3"
    _mp3(f)
    ctrl.scan([ingest])
    st = f.stat()
    os.utime(f, ns=(st.st_mtime_ns + 5_000_000_000, st.st_mtime_ns + 5_000_000_000))
    calls = _update_rescan(ctrl, monkeypatch)
    assert f in calls
    ctx.close()


def test_added_file_reparses_its_book(tmp_path, monkeypatch):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    _mp3(ingest / "Dune" / "02.mp3")
    calls = _update_rescan(ctrl, monkeypatch)
    assert any("Dune" in str(p) for p in calls)
    ctx.close()


def test_pre_fingerprint_book_is_stale_on_first_rescan(tmp_path, monkeypatch):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    f = ingest / "Dune" / "01.mp3"
    _mp3(f)
    ctrl.scan([ingest])
    book_id = next(iter(ctx.books.ids_in_folder(ingest / "Dune")))
    stale = ctx.books.get(book_id)
    for sf in stale.source_files:
        sf.mtime_ns = 0
    ctx.books.upsert(stale)
    calls = _update_rescan(ctrl, monkeypatch)
    assert f in calls
    ctx.close()
