from colophon.controller import AppController
from colophon.core.models import BookUnit, Finding, FindingCode, FindingSeverity, SourceFile

# Reuse the existing test context helper pattern from tests/test_controller.py.
from tests.test_controller import _ctx


def test_acknowledge_finding_persists(tmp_path):
    ctx = _ctx(tmp_path)
    d = tmp_path / "ingest" / "Legion"
    d.mkdir(parents=True)
    (d / "Legion.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([d])
    book = ctx.books.get(BookUnit.id_for(d))
    ctrl.acknowledge_finding(book, FindingCode.DUP_FORMAT)
    reloaded = ctx.books.get(BookUnit.id_for(d))
    assert FindingCode.DUP_FORMAT in reloaded.acknowledged_findings


def test_delete_corrupt_files_removes_bad_file_keeps_book(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    good = folder / "01.mp3"
    good.write_bytes(b"g")
    bad = folder / "02.mp3"
    bad.write_bytes(b"b")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [
        SourceFile(path=good, size=5_000_000, duration_seconds=1200.0, ext="mp3"),
        SourceFile(path=bad, size=5_000_000, duration_seconds=0.0, ext="mp3"),
    ]
    book.findings = [Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="corrupt")]
    ctx.books.upsert(book)

    result = ctrl.delete_corrupt_files(book)

    assert result.files_deleted == 1 and result.book_removed is False
    assert not bad.exists() and good.exists()
    reloaded = ctx.books.get(book.id)
    assert [sf.path for sf in reloaded.source_files] == [good]
    assert all(f.code is not FindingCode.EMPTY_AUDIO for f in reloaded.findings)


def test_delete_corrupt_files_keeps_file_and_reports_when_unlink_fails(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    import colophon.services.files as files_mod
    from colophon.core.models import FindingCode

    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    bad = folder / "01.mp3"
    bad.write_bytes(b"b")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [SourceFile(path=bad, size=5_000_000, duration_seconds=0.0, ext="mp3")]
    book.findings = [Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="corrupt")]
    ctx.books.upsert(book)

    monkeypatch.setattr(files_mod, "delete_files_from_disk", lambda paths: [])  # simulate unlink failure

    result = ctrl.delete_corrupt_files(book)

    assert result.files_deleted == 0
    assert result.book_removed is False
    assert result.errors  # a failure was reported
    reloaded = ctx.books.get(book.id)
    assert [sf.path for sf in reloaded.source_files] == [bad]  # file kept
    assert any(f.code is FindingCode.EMPTY_AUDIO for f in reloaded.findings)  # finding retained


def test_delete_corrupt_files_removes_book_when_all_bad(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    bad = folder / "01.mp3"
    bad.write_bytes(b"b")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [SourceFile(path=bad, size=5_000_000, duration_seconds=0.0, ext="mp3")]
    book.findings = [Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="corrupt")]
    ctx.books.upsert(book)

    result = ctrl.delete_corrupt_files(book)

    assert result.book_removed is True and not bad.exists()
    assert ctx.books.get(book.id) is None
