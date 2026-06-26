from colophon.controller import AppController
from colophon.core.models import BookState, BookUnit, Phase, PhaseState
from colophon.core.phases import mark, resync_state, state_of
from tests.test_controller import _ctx


def test_invalidate_hydrates_legacy_book_without_corruption(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    d = ingest / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([d])
    book = ctx.books.get(BookUnit.id_for(d))
    # Simulate a legacy row: no phase map, persisted as ORGANIZED, sources moved away.
    book.phases = {}
    book.state = BookState.ORGANIZED
    ctx.books.upsert(book)
    (d / "Book.mp3").unlink()                     # sources gone after organize/delete
    prior_files = len(book.source_files)
    assert prior_files >= 1

    ctrl.edit_field(book, "title", "A New Title")
    refreshed = ctx.books.get(BookUnit.id_for(d))
    assert refreshed.state is not BookState.FAILED            # not corrupted
    assert len(refreshed.source_files) == prior_files         # source list preserved
    assert state_of(refreshed, Phase.ORGANIZE) is PhaseState.STALE   # cascade, not wiped


def test_invalidate_reruns_local_stales_deferred(tmp_path):
    ingest = tmp_path / "ingest"
    ctx = _ctx(tmp_path)
    # ctx does not pre-register scan_paths; add ingest so _root_for can find it
    ctx.config.scan_paths = [ingest]

    d = ingest / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctx.books.get(BookUnit.id_for(d))
    for p in (Phase.MATCH, Phase.TAG):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)

    ctrl.invalidate(book, Phase.IDENTIFY)
    refreshed = ctx.books.get(BookUnit.id_for(d))
    assert state_of(refreshed, Phase.IDENTIFY) is PhaseState.FRESH   # local re-ran
    assert state_of(refreshed, Phase.MATCH) is PhaseState.STALE      # deferred staled
    assert state_of(refreshed, Phase.TAG) is PhaseState.STALE


def test_edit_field_stales_writers_not_encode(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    d = ingest / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([d])
    book = ctx.books.get(BookUnit.id_for(d))
    for p in (Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)

    ctrl.edit_field(book, "title", "A New Title")
    edited = ctx.books.get(BookUnit.id_for(d))
    assert state_of(edited, Phase.TAG) is PhaseState.STALE
    assert state_of(edited, Phase.ORGANIZE) is PhaseState.STALE
    assert state_of(edited, Phase.ENCODE) is PhaseState.FRESH       # audio untouched (override)


def test_books_by_state_and_by_phase(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    a = BookUnit.new(source_folder=tmp_path / "a")
    mark(a, Phase.IDENTIFY, PhaseState.FRESH)
    a.manually_confirmed = True
    resync_state(a)                                  # -> READY (manual)
    b = BookUnit.new(source_folder=tmp_path / "b")
    mark(b, Phase.MATCH, PhaseState.STALE)
    ctx.books.upsert(a)
    ctx.books.upsert(b)

    assert a.id in {x.id for x in ctrl.books_by_state(BookState.READY)}
    assert b.id in {x.id for x in ctrl.books_with_phase(Phase.MATCH, PhaseState.STALE)}
