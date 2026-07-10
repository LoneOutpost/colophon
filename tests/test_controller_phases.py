import pytest

from colophon.controller import AppController
from colophon.core.models import BookState, BookUnit, Phase, PhaseState, SourceFile
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


def test_phase_membership_groups_by_fresh_phase(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    a = BookUnit.new(source_folder=tmp_path / "a")
    mark(a, Phase.SEARCH, PhaseState.FRESH)
    mark(a, Phase.IDENTIFY, PhaseState.FRESH)
    b = BookUnit.new(source_folder=tmp_path / "b")
    mark(b, Phase.SEARCH, PhaseState.FRESH)
    mark(b, Phase.MATCH, PhaseState.STALE)      # STALE must NOT count

    m = ctrl.phase_membership([a, b])
    assert {x.id for x in m[Phase.SEARCH]} == {a.id, b.id}
    assert {x.id for x in m[Phase.IDENTIFY]} == {a.id}
    assert m[Phase.MATCH] == []
    assert m[Phase.ENCODE] == []
    assert set(m.keys()) == set(Phase)


def test_rerun_phase_local_routes_through_invalidate(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    d = ingest / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([d])
    book = ctx.books.get(BookUnit.id_for(d))
    for p in (Phase.MATCH, Phase.TAG):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)

    ctrl.rerun_phase([book], Phase.IDENTIFY)
    refreshed = ctx.books.get(BookUnit.id_for(d))
    assert state_of(refreshed, Phase.IDENTIFY) is PhaseState.FRESH
    assert state_of(refreshed, Phase.MATCH) is PhaseState.STALE


def test_rerun_phase_deferred_raises(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=tmp_path / "x")
    with pytest.raises(NotImplementedError):
        ctrl.rerun_phase([book], Phase.ENCODE)


async def test_write_tags_marks_tag_phase_fresh_then_edit_restales(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [tmp_path]
    d = tmp_path / "Author" / "Book"
    d.mkdir(parents=True)
    f = d / "01.mp3"
    f.write_bytes(b"")
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=d)
    book.title = "Mistborn"
    book.authors = ["Brandon Sanderson"]
    book.source_files = [SourceFile(path=f, size=1, duration_seconds=60.0, ext="mp3")]
    mark(book, Phase.IDENTIFY, PhaseState.FRESH)   # coherent prior state; TAG still PENDING
    resync_state(book)
    ctx.books.upsert(book)
    assert state_of(book, Phase.TAG) is PhaseState.PENDING

    result = await ctrl.write_tags(book)
    assert result.ok and result.written == 1
    tagged = ctx.books.get(book.id)
    assert state_of(tagged, Phase.TAG) is PhaseState.FRESH      # write made on-disk tags current

    ctrl.edit_field(tagged, "title", "A Different Title")       # later field edit re-stales it
    edited = ctx.books.get(book.id)
    assert state_of(edited, Phase.TAG) is PhaseState.STALE


async def test_write_tags_leaves_tag_phase_stale_on_failed_write(tmp_path):
    ctx = _ctx(tmp_path)
    d = tmp_path / "Author" / "Book"
    d.mkdir(parents=True)
    missing = d / "gone" / "gone.mp3"               # parent dir absent -> save() fails
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=d)
    book.title = "Mistborn"
    book.authors = ["Brandon Sanderson"]
    book.source_files = [SourceFile(path=missing, size=1, duration_seconds=60.0, ext="mp3")]
    mark(book, Phase.TAG, PhaseState.STALE)
    resync_state(book)
    ctx.books.upsert(book)

    result = await ctrl.write_tags(book)
    assert not result.ok                            # the write failed
    stale = ctx.books.get(book.id)
    assert state_of(stale, Phase.TAG) is PhaseState.STALE   # not promoted to fresh


def test_get_book_hydrates_legacy_phase_map(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=tmp_path / "legacy")
    book.authors = ["Some Author"]
    book.confidence = 100.0
    book.state = BookState.READY
    book.phases = {}                      # legacy row: no phase map
    ctx.books.upsert(book)

    got = ctrl.get_book(book.id)
    assert got is not None
    assert state_of(got, Phase.IDENTIFY) is PhaseState.FRESH   # seeded from legacy state
    assert got.state is BookState.READY                        # derived, consistent with the list


def _scan_one(tmp_path, name="Book"):
    """Scan a single empty-mp3 book under ingest; return (ctrl, ctx, book)."""
    ingest = tmp_path / "ingest"
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [ingest]
    d = ingest / "Author" / name
    d.mkdir(parents=True)
    (d / f"{name}.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    return ctrl, ctx, ctx.books.get(BookUnit.id_for(d))


def test_rerun_phase_identify_stales_downstream_except_encode(tmp_path):
    ctrl, ctx, book = _scan_one(tmp_path)
    for p in (Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)

    result = ctrl.rerun_phase([book], Phase.IDENTIFY)
    assert result.ran is Phase.IDENTIFY
    assert result.book_count == 1
    assert result.staled == {Phase.MATCH, Phase.TAG, Phase.ORGANIZE}
    assert Phase.ENCODE not in result.staled
    assert result.failed == 0
    refreshed = ctx.books.get(book.id)
    assert state_of(refreshed, Phase.ENCODE) is PhaseState.FRESH   # depends only on Search


def test_rerun_phase_search_stales_through_encode(tmp_path):
    ctrl, ctx, book = _scan_one(tmp_path)
    for p in (Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)

    result = ctrl.rerun_phase([book], Phase.SEARCH)
    assert result.staled == {Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE}


def test_rerun_phase_preserves_manual_and_matched_data(tmp_path):
    ctrl, ctx, book = _scan_one(tmp_path)
    book.title = "My Manual Title"
    book.provenance["title"] = "manual"
    book.manually_confirmed = True
    book.confidence = 100.0
    mark(book, Phase.MATCH, PhaseState.FRESH)
    ctx.books.upsert(book)

    ctrl.rerun_phase([book], Phase.IDENTIFY)
    refreshed = ctx.books.get(book.id)
    assert refreshed.title == "My Manual Title"          # reconcile only fills empty
    assert refreshed.manually_confirmed is True          # never touched
    assert refreshed.confidence == 100.0                 # never touched
    assert state_of(refreshed, Phase.MATCH) is PhaseState.STALE   # staled, not wiped


def test_rerun_phase_bulk_aggregates_across_books(tmp_path):
    ingest = tmp_path / "ingest"
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [ingest]
    for name in ("BookA", "BookB"):
        d = ingest / "Author" / name
        d.mkdir(parents=True)
        (d / f"{name}.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    books = [ctx.books.get(BookUnit.id_for(ingest / "Author" / n)) for n in ("BookA", "BookB")]
    for b in books:
        mark(b, Phase.MATCH, PhaseState.FRESH)
        ctx.books.upsert(b)

    result = ctrl.rerun_phase(books, Phase.IDENTIFY)
    assert result.book_count == 2
    assert result.staled == {Phase.MATCH}
