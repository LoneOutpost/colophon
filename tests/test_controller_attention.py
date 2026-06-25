from colophon.controller import AppController
from colophon.core.models import BookUnit, FindingCode

# Reuse the existing test context helper pattern from tests/test_controller.py.
from tests.test_controller import _ctx


def test_split_into_works_fosters_each_detected_work(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    book = ctx.books.get(BookUnit.id_for(author))
    # Force a known multi grouping for a deterministic split.
    from colophon.core.models import DetectedWork
    book.detected_works = [
        DetectedWork(label="Legion", author="Brandon Sanderson", files=[author / "Legion.mp3"]),
        DetectedWork(label="Elantris", author="Brandon Sanderson", files=[author / "Elantris.mp3"]),
    ]
    ctx.books.upsert(book)

    result = ctrl.split_into_works(book)
    assert result.fostered == 2
    assert (author / "Legion" / "Legion.mp3").is_file()
    assert (author / "Elantris" / "Elantris.mp3").is_file()
    assert ctx.books.get(BookUnit.id_for(author / "Legion")) is not None


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


def test_books_needing_attention_sorts_errors_first(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    from colophon.core.models import Finding, FindingSeverity
    warn = BookUnit.new(source_folder=tmp_path / "w")
    warn.findings = [Finding(code=FindingCode.MULTI_IN_AUTHOR, severity=FindingSeverity.WARN, detail="x")]
    err = BookUnit.new(source_folder=tmp_path / "e")
    err.findings = [Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR, detail="y")]
    ctx.books.upsert(warn)
    ctx.books.upsert(err)
    out = ctrl.books_needing_attention()
    assert [b.id for b in out][:2] == [err.id, warn.id]
