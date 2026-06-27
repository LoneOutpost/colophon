from colophon.controller import AppController
from colophon.core.models import BookUnit, FindingCode

# Reuse the existing test context helper pattern from tests/test_controller.py.
from tests.test_controller import _ctx


def test_foster_book_fosters_propagates_author_and_records_ops(tmp_path):
    ctx = _ctx(tmp_path)
    from colophon.core.models import DetectedWork
    author = tmp_path / "ingest" / "Sarah Graves"
    author.mkdir(parents=True)
    (author / "Dead Cat Bounce.mp3").write_bytes(b"")
    (author / "A Face at the Window.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    book = ctx.books.get(BookUnit.id_for(author))
    book.detected_works = [
        DetectedWork(label="Dead Cat Bounce", files=[author / "Dead Cat Bounce.mp3"]),
        DetectedWork(label="A Face at the Window", files=[author / "A Face at the Window.mp3"]),
    ]
    ctx.books.upsert(book)

    result = ctrl.foster_book(book)
    assert result.fostered == 2
    assert (author / "Dead Cat Bounce" / "Dead Cat Bounce.mp3").is_file()
    child = ctx.books.get(BookUnit.id_for(author / "Dead Cat Bounce"))
    assert child is not None
    assert child.authors == ["Sarah Graves"]          # author propagated from folder
    assert child.provenance["authors"] == "directory"
    assert result.batch_id                            # an operation batch was recorded
    assert len(ctx.operations.list_batch(result.batch_id)) == 2


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


def _multi_book(ctx, tmp_path):
    from colophon.core.models import DetectedWork
    author = tmp_path / "ingest" / "Sarah Graves"
    author.mkdir(parents=True)
    (author / "Dead Cat Bounce.mp3").write_bytes(b"")
    (author / "A Face at the Window.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    book = ctx.books.get(BookUnit.id_for(author))
    book.detected_works = [
        DetectedWork(label="Dead Cat Bounce", files=[author / "Dead Cat Bounce.mp3"]),
        DetectedWork(label="A Face at the Window", files=[author / "A Face at the Window.mp3"]),
    ]
    ctx.books.upsert(book)
    return ctrl, book


def test_is_fosterable_true_for_multi_container(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    assert ctrl.is_fosterable(book) is True


def test_is_fosterable_false_when_no_works(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    book.detected_works = []
    assert ctrl.is_fosterable(book) is False


def test_is_fosterable_false_when_finding_acknowledged(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    book.acknowledged_findings = [f.code for f in book.findings]
    assert ctrl.is_fosterable(book) is False


def test_fosterable_plan_rows_and_author(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    book.authors = ["Sarah Graves"]
    plan = ctrl.fosterable_plan(book)
    assert plan is not None
    assert plan.author == "Sarah Graves"
    assert [(w.label, w.files) for w in plan.works] == [
        ("Dead Cat Bounce", 1), ("A Face at the Window", 1)]


def test_fosterable_plan_none_when_not_fosterable(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    book.detected_works = []
    assert ctrl.fosterable_plan(book) is None


def test_fosterable_books_filters_the_set(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl, book = _multi_book(ctx, tmp_path)
    other = BookUnit.new(source_folder=tmp_path / "x")
    assert ctrl.fosterable_books([book, other]) == [book]
