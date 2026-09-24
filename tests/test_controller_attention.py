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


def _conflicted(ctx, tmp_path, *, field="title", current="Ph1", suggested="Porterhouse Blue",
                source="album tag", detail='folder "Porterhouse Blue" vs title "Ph1" (from the album tag)'):
    from colophon.core.models import Provenance

    book = BookUnit.new(source_folder=tmp_path / "Tom Sharpe" / "Porterhouse Blue")
    book.title = "Ph1"
    book.authors = ["Some Narrator"]
    book.provenance["title"] = Provenance.TAG.value
    book.provenance["authors"] = Provenance.TAG.value
    book.findings = [Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                             detail=detail, field=field, current=current, suggested=suggested,
                             source=source)]
    ctx.books.upsert(book)
    return book


def _queued_for_conflict(ctrl, book) -> bool:
    return any(book.id in {b.id for b in g.books} and "disagree" in g.phrase
               for g in ctrl.review_queue().groups)


def test_apply_finding_suggestion_writes_the_folders_title_as_a_manual_edit(tmp_path):
    from colophon.core.models import Provenance

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _conflicted(ctx, tmp_path)
    assert _queued_for_conflict(ctrl, book)

    updated = ctrl.apply_finding_suggestion(book, book.findings[0].key)

    stored = ctx.books.get(updated.id)
    assert stored.title == "Porterhouse Blue"
    assert stored.provenance["title"] == Provenance.MANUAL.value
    # The album check compares the file's raw tag, which a book edit does not change, so the
    # finding is settled by acknowledging it rather than left standing until Write tags.
    assert ctrl._active_findings(stored) == []
    assert not _queued_for_conflict(ctrl, stored)
    ctx.close()


def test_apply_finding_suggestion_is_undoable(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _conflicted(ctx, tmp_path)
    ctrl.apply_finding_suggestion(book, book.findings[0].key)
    assert ctrl.undo_last() is True
    assert ctx.books.get(book.id).title == "Ph1"
    ctx.close()


def test_apply_finding_suggestion_writes_authors(tmp_path):
    from colophon.core.models import Provenance

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _conflicted(ctx, tmp_path, field="authors", current="Some Narrator",
                       suggested="Tom Sharpe", source="tag",
                       detail="author: tag 'Some Narrator' vs folder 'Tom Sharpe'")
    updated = ctrl.apply_finding_suggestion(book, book.findings[0].key)
    stored = ctx.books.get(updated.id)
    assert stored.authors == ["Tom Sharpe"]
    assert stored.provenance["authors"] == Provenance.MANUAL.value
    assert ctrl._active_findings(stored) == []
    ctx.close()


def test_apply_finding_suggestion_refuses_a_finding_without_a_value(tmp_path):
    import pytest

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _conflicted(ctx, tmp_path, field=None, current=None, suggested=None, source=None,
                       detail='folder "Porterhouse Blue" vs tag "Ph1"')
    with pytest.raises(ValueError):
        ctrl.apply_finding_suggestion(book, book.findings[0].key)
    with pytest.raises(ValueError):
        ctrl.apply_finding_suggestion(book, "metadata_conflict:no such finding")
    assert ctx.books.get(book.id).title == "Ph1"
    ctx.close()


def test_upgrading_legacy_findings_structures_them_and_carries_the_dismissal(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    old_album = Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                        detail='folder "Porterhouse Blue" vs tag "Ph1"')
    retired = Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                      detail='author "Tom Sharpe" not in the folder path')
    book = BookUnit.new(source_folder=tmp_path / "Tom Sharpe.-.Porterhouse Blue")
    book.title = "Ph1"
    book.findings = [old_album, retired]
    book.acknowledged_findings = [old_album.key, retired.key]
    ctx.books.upsert(book)

    assert ctrl.upgrade_legacy_findings() == 1
    stored = ctx.books.get(book.id)
    [album] = stored.findings                       # the retired-check finding is gone
    assert album.detail == 'folder "Porterhouse Blue" vs title "Ph1" (from the album tag)'
    assert album.suggested == "Porterhouse Blue"
    assert stored.acknowledged_findings == [album.key]   # the dismissal followed the new wording
    assert ctrl.upgrade_legacy_findings() == 0           # idempotent
