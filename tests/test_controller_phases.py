from colophon.controller import AppController
from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import mark, state_of
from tests.test_controller import _ctx


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
