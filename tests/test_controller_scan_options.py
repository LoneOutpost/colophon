from colophon.controller import AppController
from colophon.core.models import BookUnit, ContentKind, Phase
from colophon.services.ingest import ScanOptions, ScanScope
from tests.test_controller import _ctx


def _folder(tmp_path, author, title):
    d = tmp_path / author / title
    d.mkdir(parents=True)
    (d / f"{title}.mp3").write_bytes(b"")
    return d


def test_scan_preview_new_only_skips_known(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    a = _folder(ingest, "Author A", "Book A")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])                       # ingest A as known (legacy scan)
    assert ctx.books.get(BookUnit.id_for(a)) is not None

    b = _folder(ingest, "Author B", "Book B")
    plan = ctrl.scan_preview(roots=[ingest], options=ScanOptions(scope=ScanScope.NEW_ONLY))
    folders = {u.source_folder for u in plan.units}
    assert b in folders and a not in folders


def test_scan_preview_selection_processes_only_selected(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    a = _folder(ingest, "Author A", "Book A")
    _folder(ingest, "Author B", "Book B")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book_a = ctx.books.get(BookUnit.id_for(a))
    book_a.content_kind = ContentKind.MULTI
    ctx.books.upsert(book_a)

    plan = ctrl.scan_preview(options=ScanOptions(
        scope=ScanScope.REFRESH, phases=frozenset({Phase.SEARCH, Phase.CATEGORIZE}),
        book_ids={book_a.id},
    ))
    assert {u.source_folder for u in plan.units} == {a}           # only A
    assert plan.units[0].content_kind is not ContentKind.MULTI    # forced re-classify
