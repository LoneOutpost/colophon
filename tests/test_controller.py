import asyncio
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookState, BookUnit, Provenance
from colophon.core.sources import SourceResult


class _StubSource:
    def __init__(self, name, results=None):
        # Back-compat: `_StubSource([results])` keeps the default name "stub";
        # `_StubSource("provider", [results])` names the source explicitly.
        if results is None:
            name, results = "stub", name
        self.name = name
        self._results = results

    async def search(self, query):
        return self._results


class _RecordingSource:
    def __init__(self, name="rec", results=None):
        self.name = name
        self._results = results or []
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return self._results


def _ctx(tmp_path, sources=None) -> AppContext:
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    if sources is not None:
        ctx.sources = sources
    return ctx


def _seed_ingest(tmp_path) -> Path:
    ingest = tmp_path / "ingest"
    d = ingest / "Dune"
    d.mkdir(parents=True)
    f = d / "01.mp3"
    f.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=["Frank Herbert"]))
    tags.save(f)
    return ingest


def test_scan_counts_and_persists(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    n = ctrl.scan([ingest])
    assert n == 1
    assert len(ctx.books.list_all()) == 1
    ctx.close()


def test_dashboard_stats_counts_by_state(tmp_path):
    from colophon.core.models import Phase, PhaseState
    from colophon.core.phases import mark, resync_state

    ctx = _ctx(tmp_path)
    # Book a: IDENTIFY done, manually confirmed -> derives READY
    a = BookUnit.new(source_folder=tmp_path / "a")
    mark(a, Phase.IDENTIFY, PhaseState.FRESH)
    a.manually_confirmed = True
    resync_state(a)
    assert a.state is BookState.READY
    # Book b: IDENTIFY done, no identity/confidence -> derives NEEDS_REVIEW
    b = BookUnit.new(source_folder=tmp_path / "b")
    mark(b, Phase.IDENTIFY, PhaseState.FRESH)
    resync_state(b)
    assert b.state is BookState.NEEDS_REVIEW
    ctx.books.upsert(a)
    ctx.books.upsert(b)
    stats = AppController(ctx).dashboard_stats()
    assert stats["ready"] == 1
    assert stats["needs_review"] == 1
    assert stats["total"] == 2
    ctx.close()


def test_edit_and_undo_via_controller(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.title = "Wrong"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    batch = ctrl.edit_field(b, "title", "Right")
    assert ctx.books.get(b.id).title == "Right"
    ctrl.undo(batch)
    assert ctx.books.get(b.id).title == "Wrong"
    ctx.close()


def test_process_ready_encodes_and_organizes(tmp_path, make_audio):
    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    from colophon.core.models import SourceFile
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    results = AppController(ctx).process_ready(confirm_delete=False)
    assert len(results) == 1 and results[0].organized is True
    persisted = ctx.books.get(book.id)
    assert persisted.state == BookState.ORGANIZED
    assert persisted.output_path is not None and persisted.output_path.exists()
    ctx.close()


def test_process_ready_collision_marks_failed_not_stuck_encoding(tmp_path, make_audio):
    from colophon.core.models import SourceFile
    from colophon.core.pathscheme import build_target_path

    ctx = _ctx(tmp_path)
    ctx.config.library_root = tmp_path / "lib"
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    # Pre-create the target so organize_book collides.
    target = build_target_path(ctx.config.library_root, ctx.patterns, book)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing")

    results = AppController(ctx).process_ready(confirm_delete=False)
    persisted = ctx.books.get(book.id)
    assert persisted.state == BookState.FAILED
    assert len(results) == 1
    assert results[0].organized is False
    # The failure reason is detailed (names the colliding destination) and recorded on the
    # ORGANIZE phase so At a Glance can surface it, not just returned transiently.
    assert results[0].detail is not None and "already exists" in results[0].detail
    from colophon.core.models import Phase, PhaseState
    assert persisted.phases[Phase.ORGANIZE].state is PhaseState.FAILED
    assert "already exists" in (persisted.phases[Phase.ORGANIZE].detail or "")
    ctx.close()


def test_process_book_catches_unexpected_error_and_records_reason(tmp_path, make_audio, monkeypatch):
    # An unexpected failure inside the persist worker must not escape the run with no explanation:
    # it lands as a FAILED phase carrying the reason, and a failed result — so At a Glance can show it.
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import Phase, PhaseState, SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)

    def _boom(_book, _opts):
        raise PermissionError("disk is read-only")

    monkeypatch.setattr(ctrl, "_persist_book", _boom)
    res = ctrl._process_book(book, EncodeJobOptions(encode=False, organize=True))

    assert res.status == "failed"
    assert "disk is read-only" in (res.detail or "")
    persisted = ctx.books.get(book.id)
    assert persisted.phases[Phase.ORGANIZE].state is PhaseState.FAILED
    assert "PermissionError" in (persisted.phases[Phase.ORGANIZE].detail or "")
    assert "disk is read-only" in (persisted.phases[Phase.ORGANIZE].detail or "")
    ctx.close()


def test_process_ready_encode_failure_marks_failed(tmp_path, make_audio):
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    # Claim a 100s duration on a real 1s file so verification fails.
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=100.0, ext="mp3")]
    ctx.books.upsert(book)

    results = AppController(ctx).process_ready(confirm_delete=False)
    persisted = ctx.books.get(book.id)
    assert persisted.state == BookState.FAILED
    assert len(results) == 1
    assert results[0].encoded is False
    assert results[0].detail is not None
    ctx.close()


def test_undo_last_via_controller(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.title = "Wrong"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.edit_field(b, "title", "Right")
    assert ctx.books.get(b.id).title == "Right"
    assert ctrl.undo_last() is True
    assert ctx.books.get(b.id).title == "Wrong"
    ctx.close()


def _book_in(ctx, folder):
    folder.mkdir(parents=True, exist_ok=True)
    b = BookUnit.new(source_folder=folder)
    b.title = "Wrong"
    ctx.books.upsert(b)
    return b


def test_edit_field_does_not_write_datafile(tmp_path):
    # colophon no longer mirrors edits into metadata.json — that file is AudiobookShelf's domain.
    ctx = _ctx(tmp_path)
    b = _book_in(ctx, tmp_path / "ingest" / "x")
    ctrl = AppController(ctx)
    ctrl.edit_field(b, "title", "Right")
    assert ctx.books.get(b.id).title == "Right"                # DB is updated
    assert not (b.source_folder / "metadata.json").exists()    # no datafile sidecar written
    ctx.close()


def test_bulk_normalize_titlecases_across_books_one_batch(tmp_path):
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.title = "the hobbit"
    a.authors = ["frank herbert"]
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.title = "dune messiah"
    ctx.books.upsert(a)
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    batch = ctrl.bulk_normalize([a, b], ["title", "author"])
    assert ctx.books.get(a.id).title == "The Hobbit"
    assert ctx.books.get(a.id).authors == ["Frank Herbert"]
    assert ctx.books.get(b.id).title == "Dune Messiah"
    ctrl.undo(batch)  # one undoable batch reverts every change
    assert ctx.books.get(a.id).title == "the hobbit"
    assert ctx.books.get(a.id).authors == ["frank herbert"]
    ctx.close()


def test_bulk_normalize_skips_empty_and_unchanged(tmp_path):
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.title = "Already Fine"  # already normal -> no change; author/series empty -> skipped
    ctx.books.upsert(a)
    AppController(ctx).bulk_normalize([a], ["title", "author", "series"])
    assert ctx.books.get(a.id).title == "Already Fine"
    ctx.close()


def test_bulk_edit_does_not_write_datafile(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book_in(ctx, tmp_path / "ingest" / "a")
    b = _book_in(ctx, tmp_path / "ingest" / "b")
    AppController(ctx).bulk_edit([a, b], "publisher", "Tor")
    for book in (a, b):
        assert ctx.books.get(book.id).publisher == "Tor"             # DB updated
        assert not (book.source_folder / "metadata.json").exists()   # no datafile sidecar written
    ctx.close()


def _book_named(ctx, tmp_path, filename):
    from colophon.core.models import SourceFile

    folder = tmp_path / "ingest" / "parse"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    p.write_bytes(b"x")
    book = BookUnit.new(source_folder=folder)
    book.source_files = [SourceFile(path=p, size=1, duration_seconds=60.0, ext="mp3")]
    ctx.books.upsert(book)
    return book


def test_book_filename_uses_first_source_file(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    assert AppController(ctx).book_filename(book) == "Brandon Sanderson - Mistborn.mp3"
    ctx.close()


def test_book_filename_falls_back_to_folder_name(tmp_path):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "ingest" / "The Way of Kings"
    folder.mkdir(parents=True)
    book = BookUnit.new(source_folder=folder)
    ctx.books.upsert(book)
    assert AppController(ctx).book_filename(book) == "The Way of Kings"
    ctx.close()


def test_embedded_tags_reads_first_source_file(tmp_path):
    from mutagen.id3 import ID3, TIT2, TPE1

    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Cujo.mp3")
    path = book.source_files[0].path
    id3 = ID3()
    id3.add(TIT2(encoding=3, text=["Cujo"]))
    id3.add(TPE1(encoding=3, text=["Stephen King"]))
    id3.save(path)

    tags = AppController(ctx).embedded_tags(book)
    assert tags is not None
    assert tags.title == "Cujo"
    assert tags.artist == "Stephen King"
    ctx.close()


def test_embedded_tags_none_when_no_source_files(tmp_path):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "ingest" / "Empty"
    folder.mkdir(parents=True)
    book = BookUnit.new(source_folder=folder)
    ctx.books.upsert(book)
    assert AppController(ctx).embedded_tags(book) is None
    ctx.close()


def test_preview_filename_parse_returns_fields(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    parsed = AppController(ctx).preview_filename_parse(book, "$Author - $Title")
    assert parsed == {"author": "Brandon Sanderson", "title": "Mistborn"}
    ctx.close()


def test_preview_filename_parse_returns_empty_on_no_match(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "nomatch.mp3")
    parsed = AppController(ctx).preview_filename_parse(book, "$Author - $Title")
    assert parsed == {}
    ctx.close()


def test_preview_filename_parse_raises_on_bad_template(tmp_path):
    import pytest

    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "x.mp3")
    with pytest.raises(ValueError):
        AppController(ctx).preview_filename_parse(book, "$Bogus")
    ctx.close()


def test_apply_filename_parse_writes_selected_fields(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    n = AppController(ctx).apply_filename_parse([book], "$Author - $Title", {"author", "title"})
    assert n == 1
    got = ctx.books.get(book.id)
    assert got.title == "Mistborn"
    assert got.authors == ["Brandon Sanderson"]
    assert got.provenance["title"] == "filename"
    assert got.provenance["authors"] == "filename"
    ctx.close()


def test_apply_filename_parse_honours_field_selection(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    AppController(ctx).apply_filename_parse([book], "$Author - $Title", {"title"})
    got = ctx.books.get(book.id)
    assert got.title == "Mistborn"
    assert got.authors == []  # author parsed but not selected, so not written
    ctx.close()


def test_apply_filename_parse_sets_series_before_sequence(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    AppController(ctx).apply_filename_parse([book], "$Series #$SerNum", {"series", "sequence"})
    got = ctx.books.get(book.id)
    assert got.series and got.series[0].name == "Mistborn"
    assert got.series[0].sequence == 1.0  # sequence applied because series was set first
    ctx.close()


def test_apply_filename_parse_skips_non_matching_books(tmp_path):
    ctx = _ctx(tmp_path)
    ok = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    from colophon.core.models import SourceFile
    bad_folder = tmp_path / "ingest" / "bad"
    bad_folder.mkdir(parents=True)
    bp = bad_folder / "nomatch.mp3"
    bp.write_bytes(b"x")
    bad = BookUnit.new(source_folder=bad_folder)
    bad.source_files = [SourceFile(path=bp, size=1, duration_seconds=60.0, ext="mp3")]
    ctx.books.upsert(bad)
    n = AppController(ctx).apply_filename_parse([ok, bad], "$Author - $Title", {"author", "title"})
    assert n == 1  # only the matching book counted
    assert ctx.books.get(bad.id).title is None
    ctx.close()


def test_apply_filename_parse_drops_sequence_without_series(tmp_path):
    # A pattern that yields sequence but not series, on a book with no series,
    # is a no-op for sequence: it must not be counted or recorded.
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    ctrl = AppController(ctx)
    n = ctrl.apply_filename_parse([book], "$Title #$SerNum", {"title", "sequence"})
    assert n == 1  # title was written
    got = ctx.books.get(book.id)
    assert got.title == "Mistborn"
    assert got.series == []  # no series, so sequence was dropped
    # the recorded batch holds only the real (title) change
    changes = ctx.history.list_batch(ctx.history.latest_batch_id())
    assert {c.field for c in changes} == {"title"}
    ctx.close()


def test_apply_filename_parse_skips_book_when_only_noop_fields(tmp_path):
    # Selecting only sequence (no series) on a series-less book changes nothing,
    # so the book is not counted and no empty batch is recorded.
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    ctrl = AppController(ctx)
    n = ctrl.apply_filename_parse([book], "$Title #$SerNum", {"sequence"})
    assert n == 0
    ctx.close()


def test_filename_parse_updates_matches_apply(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    updates = AppController(ctx).filename_parse_updates(
        book, "$Title #$SerNum", {"title", "sequence"}
    )
    assert updates == {"title": "Mistborn"}  # sequence dropped, no series
    ctx.close()


def test_apply_filename_parse_is_undoable(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    ctrl = AppController(ctx)
    ctrl.apply_filename_parse([book], "$Author - $Title", {"title"})
    assert ctrl.undo_last() is True
    assert ctx.books.get(book.id).title is None
    ctx.close()


class _FakeAbs:
    def __init__(self):
        self.scanned = []

    async def scan_library(self, library_id):
        self.scanned.append(library_id)
        return "OK"


async def test_trigger_abs_scan_when_configured(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.abs_client = _FakeAbs()
    ctx.config.audiobookshelf_library_id = "lib_1"
    ok = await AppController(ctx).trigger_abs_scan()
    assert ok is True
    assert ctx.abs_client.scanned == ["lib_1"]
    ctx.close()


async def test_trigger_abs_scan_noop_when_unconfigured(tmp_path):
    ctx = _ctx(tmp_path)  # abs_client is None
    ok = await AppController(ctx).trigger_abs_scan()
    assert ok is False
    ctx.close()


def test_ready_books_returns_only_ready(tmp_path):
    ctx = _ctx(tmp_path)
    r = BookUnit.new(source_folder=tmp_path / "r")
    r.state = BookState.READY
    n = BookUnit.new(source_folder=tmp_path / "n")
    n.state = BookState.NEEDS_REVIEW
    ctx.books.upsert(r)
    ctx.books.upsert(n)
    ids = {b.id for b in AppController(ctx).ready_books()}
    assert ids == {r.id}
    ctx.close()


def test_process_ready_reports_progress(tmp_path, make_audio):
    ctx = _ctx(tmp_path)
    from colophon.core.models import SourceFile
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    seen = []
    AppController(ctx).process_ready(confirm_delete=False, progress=lambda done, total, title: seen.append((done, total, title)))
    assert seen == [(1, 1, "Dune")]
    ctx.close()


def test_process_one_encodes_and_organizes_single(tmp_path, make_audio):
    ctx = _ctx(tmp_path)
    from colophon.core.models import SourceFile
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)
    result = AppController(ctx).process_one(book, confirm_delete=False)
    assert result.organized is True
    assert ctx.books.get(book.id).state == BookState.ORGANIZED
    ctx.close()


def test_save_settings_persists_and_updates_live_config(tmp_path):
    from colophon.adapters.config import Config as _Config
    from colophon.adapters.config import load_config

    cfg_path = tmp_path / "c.toml"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"), config_path=cfg_path)
    ctrl = AppController(ctx)

    new = _Config(
        db_path=tmp_path / "db.sqlite",
        library_root=tmp_path / "library",
        review_threshold=80.0,
        transcode_bitrate="128k",
        audiobookshelf_url="http://abs.local",
        audiobookshelf_token="tok",
    )
    ctrl.save_settings(new)

    # persisted to disk
    assert load_config(cfg_path) == new
    # live config updated in place
    assert ctx.config.review_threshold == 80.0
    assert ctx.config.audiobookshelf_url == "http://abs.local"
    ctx.close()


def test_save_settings_rediscovers_abs_agg_sources(tmp_path, monkeypatch):
    import colophon.app_context as ctxmod
    from colophon.adapters.config import Config as _Config
    from colophon.adapters.sources.abs_agg import AbsAggSource

    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"), config_path=tmp_path / "c.toml")
    ctrl = AppController(ctx)
    base_count = len(ctx.sources)  # built-ins only; abs_agg_url unset at startup

    monkeypatch.setattr(
        ctxmod, "discover_providers",
        lambda url: [AbsAggSource(provider="goodreads", label="Goodreads", base_url=url)] if url else [],
    )

    # adding the URL makes providers appear live (no restart)
    ctrl.save_settings(_Config(db_path=tmp_path / "db.sqlite", abs_agg_url="http://abs-agg"))
    assert "goodreads" in {s.name for s in ctx.sources}
    assert len(ctx.sources) == base_count + 1

    # saving again with the same URL does not duplicate
    ctrl.save_settings(_Config(db_path=tmp_path / "db.sqlite", abs_agg_url="http://abs-agg"))
    assert len(ctx.sources) == base_count + 1

    # clearing the URL removes the abs-agg sources
    ctrl.save_settings(_Config(db_path=tmp_path / "db.sqlite"))
    assert "goodreads" not in {s.name for s in ctx.sources}
    assert len(ctx.sources) == base_count
    ctx.close()


def test_get_matches_returns_ranked(tmp_path):
    import asyncio as _asyncio

    src = _StubSource("openlibrary", [
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="openlibrary", title="Dune Messiah", authors=["Frank Herbert"]),
    ])
    ctx = _ctx(tmp_path, sources=[src])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    matches = _asyncio.run(AppController(ctx).get_matches(book))
    assert matches[0].title == "Dune"  # best match ranked first
    ctx.close()


def test_apply_match_sets_fields_with_provider_provenance(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "wrong"
    ctx.books.upsert(book)
    result = SourceResult(
        provider="audnexus", title="Dune", authors=["Frank Herbert"],
        narrators=["Scott Brick"], series_name="Dune", series_sequence=1.0,
        publish_year=2007, asin="B002V1A0WE", description="desc",
    )
    ctrl = AppController(ctx)
    ctrl.apply_match(book, result)
    persisted = ctx.books.get(book.id)
    assert persisted.title == "Dune"
    assert persisted.authors == ["Frank Herbert"]
    assert persisted.narrators == ["Scott Brick"]
    assert persisted.series[0].name == "Dune"
    assert persisted.series[0].sequence == 1.0
    assert persisted.publish_year == 2007
    assert persisted.asin == "B002V1A0WE"
    assert persisted.provenance["title"] == "audnexus"
    assert persisted.provenance["authors"] == "audnexus"  # list field stored under model key
    assert not (book.source_folder / "metadata.json").exists()  # colophon does not write datafile sidecars
    ctx.close()


def test_apply_match_records_the_match_phase(tmp_path):
    from colophon.core.models import Phase, PhaseState
    from colophon.core.phases import mark, state_of

    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "y")
    book.source_folder.mkdir(parents=True)
    # Simulate a scanned book (local phases fresh) so invalidate(TAG)'s refresh_local
    # leaves them alone; MATCH starts PENDING so a fresh MATCH proves apply_match set it.
    for p in (Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY):
        mark(book, p, PhaseState.FRESH)
    ctx.books.upsert(book)
    result = SourceResult(
        provider="audnexus", title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE",
    )
    AppController(ctx).apply_match(book, result)
    persisted = ctx.books.get(book.id)
    # An online-source match records the MATCH phase.
    assert state_of(persisted, Phase.MATCH) is PhaseState.FRESH
    assert persisted.state in (BookState.READY, BookState.NEEDS_REVIEW)
    ctx.close()


def test_get_matches_empty_when_no_sources(tmp_path):
    ctx = _ctx(tmp_path, sources=[])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    matches = asyncio.run(AppController(ctx).get_matches(book))
    assert matches == []


def test_known_authors_distinct_and_sorted(tmp_path):
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.authors = ["Brandon Sanderson", "Co Author"]
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.authors = ["Brandon Sanderson"]
    c = BookUnit.new(source_folder=tmp_path / "c")  # no authors
    for x in (a, b, c):
        ctx.books.upsert(x)
    assert AppController(ctx).known_authors() == ["Brandon Sanderson", "Co Author"]
    ctx.close()


def test_known_series_distinct_and_sorted(tmp_path):
    from colophon.core.models import SeriesRef

    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.series = [SeriesRef(name="Stormlight Archive")]
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.series = [SeriesRef(name="Mistborn")]
    c = BookUnit.new(source_folder=tmp_path / "c")
    c.series = [SeriesRef(name="Mistborn")]
    for x in (a, b, c):
        ctx.books.upsert(x)
    assert AppController(ctx).known_series() == ["Mistborn", "Stormlight Archive"]
    ctx.close()


def test_known_authors_and_series_empty_library(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    assert ctrl.known_authors() == []
    assert ctrl.known_series() == []
    ctx.close()


def test_available_sources_lists_configured_with_labels(tmp_path):
    ctx = _ctx(tmp_path)  # default: audnexus, openlibrary, googlebooks (no hardcover token)
    labels = dict(AppController(ctx).available_sources())
    assert labels["audnexus"] == "Audible"
    assert labels["googlebooks"] == "Google Books"
    assert "hardcover" not in labels  # not configured without a token
    ctx.close()


def test_available_sources_and_label_prefer_source_label_attr(tmp_path):
    ctx = _ctx(tmp_path)

    class _Labeled:
        name = "librivox"
        label = "LibriVox"

        async def search(self, query):
            return []

    ctx.sources.append(_Labeled())
    ctrl = AppController(ctx)
    labels = dict(ctrl.available_sources())
    assert labels["librivox"] == "LibriVox"
    assert ctrl.source_label("librivox") == "LibriVox"
    # built-in sources without a .label still use the static map
    assert ctrl.source_label("audnexus") == "Audible"
    ctx.close()


async def test_search_matches_queries_only_chosen_source(tmp_path):
    audn = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune")])
    other = _StubSource("openlibrary", [SourceResult(provider="openlibrary", title="WRONG")])
    ctx = _ctx(tmp_path, sources=[audn, other])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    results = await AppController(ctx).search_matches(
        book, title="Dune", author="Frank Herbert", series=None, asin=None, source_name="audnexus"
    )
    assert results and all(r.provider == "audnexus" for r in results)
    ctx.close()


async def test_search_matches_builds_query_from_edited_fields(tmp_path):
    captured = {}

    class RecSource:
        name = "audnexus"

        async def search(self, query):
            captured["q"] = query
            return []

    ctx = _ctx(tmp_path, sources=[RecSource()])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Original"
    await AppController(ctx).search_matches(
        book, title="Edited", author="A", series="S", asin="B01",
        isbn="9780306406157", source_name="audnexus"
    )
    q = captured["q"]
    assert (q.title, q.author, q.series, q.asin, q.isbn) == (
        "Edited", "A", "S", "B01", "9780306406157"
    )
    ctx.close()


async def test_search_matches_blank_fields_become_none(tmp_path):
    captured = {}

    class RecSource:
        name = "audnexus"

        async def search(self, query):
            captured["q"] = query
            return []

    ctx = _ctx(tmp_path, sources=[RecSource()])
    book = BookUnit.new(source_folder=tmp_path / "x")
    await AppController(ctx).search_matches(
        book, title="Dune", author="  ", series="", asin=None, source_name="audnexus"
    )
    q = captured["q"]
    assert q.title == "Dune" and q.author is None and q.series is None and q.asin is None
    ctx.close()


async def test_search_matches_unknown_source_returns_empty(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    results = await AppController(ctx).search_matches(
        book, title="Dune", author=None, series=None, asin=None, source_name="nope"
    )
    assert results == []
    ctx.close()


async def test_search_matches_source_error_returns_empty(tmp_path):
    class BoomSource:
        name = "audnexus"

        async def search(self, query):
            raise RuntimeError("boom")

    ctx = _ctx(tmp_path, sources=[BoomSource()])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    results = await AppController(ctx).search_matches(
        book, title="Dune", author=None, series=None, asin=None, source_name="audnexus"
    )
    assert results == []
    ctx.close()
    ctx.close()


async def test_book_cover_serves_cached_file(tmp_path):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "bk"
    folder.mkdir()
    cover = folder / "cover.jpg"
    cover.write_bytes(b"JPGDATA")
    b = BookUnit.new(source_folder=folder)
    b.cover_path = cover
    ctx.books.upsert(b)
    result = await AppController(ctx).book_cover(b.id)
    assert result == (b"JPGDATA", "image/jpeg")
    ctx.close()


async def test_book_cover_fetches_and_caches_from_url(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "bk"
    folder.mkdir()
    b = BookUnit.new(source_folder=folder)
    b.cover_url = "http://x/c.png"
    ctx.books.upsert(b)

    async def fake_ensure(book, *, dest_dir, client=None):
        p = dest_dir / "cover.png"
        p.write_bytes(b"PNGDATA")
        book.cover_path = p
        return p

    monkeypatch.setattr("colophon.controller.ensure_cached_cover", fake_ensure)
    result = await AppController(ctx).book_cover(b.id)
    assert result == (b"PNGDATA", "image/png")
    # cover_path is persisted so the next request serves the cached file
    assert ctx.books.get(b.id).cover_path == folder / "cover.png"
    ctx.close()


async def test_book_cover_none_when_no_cover(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "bk")
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    assert await ctrl.book_cover(b.id) is None
    assert await ctrl.book_cover("does-not-exist") is None
    ctx.close()


def test_apply_match_partial_result_only_sets_present_fields(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "wrong"
    book.authors = ["Keep Me"]
    ctx.books.upsert(book)
    result = SourceResult(provider="openlibrary", title="New Title")
    batch = AppController(ctx).apply_match(book, result)
    persisted = ctx.books.get(book.id)
    assert persisted.title == "New Title"
    assert persisted.authors == ["Keep Me"]  # absent source fields don't clobber
    changes = ctx.history.list_batch(batch)
    assert len(changes) == 1
    assert changes[0].field == "title"
    ctx.close()


def test_save_fields_applies_updates_with_manual_provenance(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "Old"
    ctx.books.upsert(book)
    AppController(ctx).save_fields(book, {"title": "New", "author": "Frank Herbert"})
    persisted = ctx.books.get(book.id)
    assert persisted.title == "New"
    assert persisted.authors == ["Frank Herbert"]
    assert persisted.provenance["title"] == "manual"
    assert persisted.provenance["authors"] == "manual"
    ctx.close()


def test_save_fields_is_undoable(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "y")
    book.source_folder.mkdir(parents=True)
    book.title = "Original"
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    batch = ctrl.save_fields(book, {"title": "Changed"})
    ctrl.undo(batch)
    assert ctx.books.get(book.id).title == "Original"
    ctx.close()


def _book_with_files(ctx, tmp_path, names):
    from colophon.core.models import SourceFile

    folder = tmp_path / "ingest" / "bk"
    folder.mkdir(parents=True)
    book = BookUnit.new(source_folder=folder)
    sfs = []
    for n in names:
        p = folder / n
        p.write_bytes(b"x")
        sfs.append(SourceFile(path=p, size=1, duration_seconds=60.0, ext="mp3"))
    book.source_files = sfs
    ctx.books.upsert(book)
    return book


def test_move_file_reorders_and_persists(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_with_files(ctx, tmp_path, ["01.mp3", "02.mp3", "03.mp3"])
    target = book.source_files[2].path  # 03.mp3
    AppController(ctx).move_file(book, target, -1)  # move up one
    persisted = ctx.books.get(book.id)
    assert [sf.path.name for sf in persisted.source_files] == ["01.mp3", "03.mp3", "02.mp3"]
    ctx.close()


def test_exclude_file_persists(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_with_files(ctx, tmp_path, ["01.mp3", "02.mp3"])
    AppController(ctx).exclude_file(book, book.source_files[0].path)
    assert [sf.path.name for sf in ctx.books.get(book.id).source_files] == ["02.mp3"]
    ctx.close()


def test_rename_file_success_and_collision(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_with_files(ctx, tmp_path, ["01.mp3", "02.mp3"])
    ctrl = AppController(ctx)
    new = ctrl.rename_file(book, book.source_files[0].path, "00 - Intro.mp3")
    assert new is not None and new.name == "00 - Intro.mp3"
    assert ctx.books.get(book.id).source_files[0].path.name == "00 - Intro.mp3"
    # collision returns None and does not change anything
    collide = ctrl.rename_file(book, book.source_files[1].path, "00 - Intro.mp3")
    assert collide is None
    ctx.close()


def test_rename_file_bad_name_returns_none(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_with_files(ctx, tmp_path, ["01.mp3", "02.mp3"])
    ctrl = AppController(ctx)
    before = [sf.path.name for sf in book.source_files]
    # all-whitespace name is caught (ValueError) -> None, no crash, list unchanged
    assert ctrl.rename_file(book, book.source_files[0].path, "  ") is None
    assert [sf.path.name for sf in ctx.books.get(book.id).source_files] == before
    ctx.close()


def test_tag_plan_and_write_tags_roundtrip(tmp_path):
    from colophon.adapters.tags import read_embedded_tags, write_embedded_tags
    from colophon.core.models import EmbeddedTags, SourceFile

    ctx = _ctx(tmp_path)
    f = tmp_path / "ingest" / "01.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Old"))
    book = BookUnit.new(source_folder=f.parent)
    book.title = "New Title"
    book.authors = ["Author"]
    book.source_files = [SourceFile(path=f, size=1, duration_seconds=60.0, ext="mp3")]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)

    plan = ctrl.tag_plan(book)
    assert "title" in plan.files[0].changed_fields

    result = asyncio.run(ctrl.write_tags(book))
    assert result.written == 1
    assert read_embedded_tags(f).title == "New Title"

    assert ctrl.undo_tag_batch() is True
    assert read_embedded_tags(f).title == "Old"
    ctx.close()


async def test_write_tags_books_reports_progress_per_book(tmp_path):
    ctx = _ctx(tmp_path)
    books = []
    for i in range(3):
        b = BookUnit.new(source_folder=tmp_path / f"b{i}")
        b.title = f"Book {i}"
        ctx.books.upsert(b)  # no source_files: commit_tag is a 0-file no-op
        books.append(b)
    ctrl = AppController(ctx)

    seen: list[tuple[int, str]] = []
    results = await ctrl.write_tags_books(
        books, progress=lambda done, book, result: seen.append((done, book.id))
    )
    assert len(results) == 3
    # called once per book, with a 1-based increasing count, in order
    assert [d for d, _ in seen] == [1, 2, 3]
    assert [bid for _, bid in seen] == [b.id for b in books]
    ctx.close()


def test_write_tags_books_skips_blocking_error(tmp_path):
    """A book with a blocking error (corrupt/unreadable audio) is never written — the backstop
    skips it so a stale UI can't trigger a failing write."""
    from colophon.adapters.tags import read_embedded_tags, write_embedded_tags
    from colophon.core.models import (
        EmbeddedTags,
        Finding,
        FindingCode,
        FindingSeverity,
        SourceFile,
    )

    ctx = _ctx(tmp_path)
    f = tmp_path / "ingest" / "01.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Old"))
    book = BookUnit.new(source_folder=f.parent)
    book.title = "New Title"
    book.source_files = [SourceFile(path=f, size=1, duration_seconds=0.0, ext="mp3")]
    book.findings = [
        Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="corrupt")
    ]
    ctx.books.upsert(book)

    (result,) = asyncio.run(AppController(ctx).write_tags_books([book]))
    assert result.written == 0  # skipped, not written
    assert read_embedded_tags(f).title == "Old"  # the file was left untouched
    ctx.close()


def test_reprobe_book_clears_flag_when_file_recovers(tmp_path, make_audio):
    from colophon.core.models import Finding, FindingCode, FindingSeverity, SourceFile

    ctx = _ctx(tmp_path)
    f = make_audio("b/01.m4b", seconds=1)  # a real, readable file
    book = BookUnit.new(source_folder=f.parent)
    # Simulate a stale 0-duration read that had produced an EMPTY_AUDIO finding.
    book.source_files = [SourceFile(path=f, size=f.stat().st_size, duration_seconds=0.0, ext="m4b")]
    book.findings = [Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="x")]
    ctx.books.upsert(book)

    changed = AppController(ctx).reprobe_book(book)
    assert changed is True
    reloaded = ctx.books.get(book.id)
    assert reloaded.source_files[0].duration_seconds > 0
    assert all(fd.code is not FindingCode.EMPTY_AUDIO for fd in reloaded.findings)
    ctx.close()


def test_process_book_skips_blocking_error(tmp_path):
    """The encode/organize worker refuses a book whose files are gone — status 'skipped'."""
    from colophon.controller import EncodeJobOptions

    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "b")
    book.missing = True
    ctx.books.upsert(book)
    result = AppController(ctx)._process_book(book, EncodeJobOptions(encode=True, organize=True))
    assert result.status == "skipped"
    assert "blocking" in (result.detail or "")
    ctx.close()


def test_process_one_embeds_tags_into_the_m4b(tmp_path, make_audio):
    from colophon.adapters.tags import read_embedded_tags
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.publish_year = 1965
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    result = AppController(ctx).process_one(book)
    assert result.organized is True
    organized = ctx.books.get(book.id)
    out = organized.output_path
    assert out is not None and out.suffix == ".m4b"
    tags = read_embedded_tags(out)
    assert tags.title == "Dune" and tags.artist == "Frank Herbert" and tags.year == 1965
    ops = ctx.operations.list_batch(ctx.operations.latest_batch_id())
    types = {op.op_type for op in ops}
    assert "tag_write" in types and "organize" in types
    # the tag_write op targets the FINAL organized M4B, not the staging path
    tag_op = next(op for op in ops if op.op_type == "tag_write")
    assert tag_op.target == str(out)
    ctx.close()


def test_apply_match_fields_applies_only_selected_and_captures_cover(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "b")
    book.title = "Old Title"
    ctx.books.upsert(book)
    result = SourceResult(
        provider="audnexus", title="New Title", authors=["Brandon Sanderson"],
        publish_year=2006, cover_url="https://covers.example/x.jpg",
    )
    AppController(ctx).apply_match_fields(book, result, {"title", "cover"})
    got = ctx.books.get(book.id)
    assert got.title == "New Title"
    assert got.authors == []
    assert got.publish_year is None
    assert got.cover_url == "https://covers.example/x.jpg"
    assert got.provenance.get("title") == "audnexus"
    ctx.close()


def test_apply_match_applies_all_present_fields_and_cover(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "b")
    ctx.books.upsert(book)
    result = SourceResult(
        provider="audnexus", title="T", authors=["A"], narrators=["N"],
        series_name="S", series_sequence=1.0, publish_year=2006, asin="B00", cover_url="https://c/x.jpg",
    )
    AppController(ctx).apply_match(book, result)
    got = ctx.books.get(book.id)
    assert got.title == "T" and got.authors == ["A"] and got.narrators == ["N"]
    assert got.series[0].name == "S" and got.publish_year == 2006 and got.asin == "B00"
    assert got.cover_url == "https://c/x.jpg"
    ctx.close()


def test_apply_match_fields_captures_abridged(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    ctx.books.upsert(b)
    result = SourceResult(provider="stub", title="Dune", abridged=True)
    ctrl.apply_match_fields(b, result, {"title"})
    assert ctx.books.get(b.id).abridged is True
    ctx.close()


def test_set_abridged_persists(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    ctx.books.upsert(b)
    ctrl.set_abridged(b, False)
    assert ctx.books.get(b.id).abridged is False
    ctx.close()


def test_rd_configured_reflects_token(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext

    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", real_debrid_token="t"))
    assert AppController(ctx).rd_configured() is True
    ctx.close()

    ctx2 = AppContext.create(Config(db_path=tmp_path / "db2.sqlite"))
    assert AppController(ctx2).rd_configured() is False
    ctx2.close()


async def test_rd_test_connection_uses_passed_token_without_config(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdUser

    ctx = _ctx(tmp_path)  # no token configured
    ctrl = AppController(ctx)
    captured = {}

    class FakeClient:
        def __init__(self, token, **kwargs):
            captured["token"] = token

        async def user(self):
            return RdUser(id=1, username="demo")

        async def aclose(self):
            pass

    monkeypatch.setattr("colophon.controller.RealDebridClient", FakeClient)
    user = await ctrl.rd_test_connection("typed-token")
    assert user.username == "demo"
    assert captured["token"] == "typed-token"  # tested the passed token, not config
    assert ctx.config.real_debrid_token is None  # config not mutated
    ctx.close()


def test_rd_download_dir_defaults_under_data_dir(tmp_path):
    from colophon.app_context import default_db_path

    ctx = _ctx(tmp_path)
    got = AppController(ctx)._rd_download_dir()
    assert got == default_db_path().parent / "downloads"
    ctx.close()


async def test_rd_download_ingests_downloaded_folder(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdTorrentInfo
    from colophon.services.acquire import AcquiredFile, AcquireResult

    ctx = _ctx(tmp_path)
    ctx.config.real_debrid_token = "t"
    ctx.config.real_debrid_download_dir = tmp_path / "dl"
    ctrl = AppController(ctx)

    class FakeClient:
        async def torrent_info(self, tid):
            return RdTorrentInfo(id=tid, filename="Mistborn", status="downloaded", links=["L1"])
        async def aclose(self):
            pass

    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())

    async def fake_download(client, torrent, dest_root, *, folder=None, file_ids=None,
                            progress=None, byte_progress=None, cancel=None, mode=None):
        folder = folder or dest_root / "Mistborn"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01.mp3").write_bytes(b"")
        return AcquireResult(folder=folder, files=[AcquiredFile("01.mp3", folder / "01.mp3", True)])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    result, book_ids = await ctrl.rd_download("a")
    assert result.any_ok is True
    assert len(book_ids) == 1
    assert ctx.books.get(book_ids[0]) is not None
    ctx.close()


async def test_resume_download_reuses_the_interrupted_folder(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdTorrentInfo
    from colophon.services.acquire import AcquiredFile, AcquireResult

    ctx = _ctx(tmp_path)
    ctx.config.real_debrid_token = "t"
    ctx.config.real_debrid_download_dir = tmp_path / "dl"
    ctrl = AppController(ctx)

    class FakeClient:
        async def torrent_info(self, tid):
            return RdTorrentInfo(id=tid, filename="Mistborn", status="downloaded", links=["L1"])
        async def aclose(self):
            pass

    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())

    folders_seen: list = []

    async def fake_download(client, torrent, dest_root, *, folder=None, file_ids=None,
                            progress=None, byte_progress=None, cancel=None, mode=None):
        folders_seen.append(folder)
        used = folder or (dest_root / "Mistborn-7")  # a deduped name on the first call
        used.mkdir(parents=True, exist_ok=True)
        (used / "01.mp3").write_bytes(b"")
        # the first call is cancelled (paused), so nothing ingests; the resume succeeds
        ok = folder is not None
        files = [AcquiredFile("01.mp3", used / "01.mp3", ok, None if ok else "cancelled")]
        return AcquireResult(folder=used, files=files)

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    await ctrl.rd_download("tid", name="Mistborn")
    assert ctrl.active_downloads()[0].status == "paused"
    assert folders_seen[0] is None  # first attempt allocates a fresh folder

    await ctrl.resume_download("tid")
    # the resume must pass the SAME folder the first attempt used, so stream_download resumes its .part
    assert folders_seen[1] == tmp_path / "dl" / "Mistborn-7"
    assert ctrl.active_downloads()[0].status == "done"
    ctx.close()


async def test_rd_download_threads_file_ids(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdTorrentInfo
    from colophon.services.acquire import AcquiredFile, AcquireResult

    ctx = _ctx(tmp_path)
    ctx.config.real_debrid_token = "t"
    ctx.config.real_debrid_download_dir = tmp_path / "dl"
    ctrl = AppController(ctx)

    class FakeClient:
        async def torrent_info(self, tid):
            return RdTorrentInfo(id=tid, filename="Bundle", status="downloaded", links=["L1"])
        async def aclose(self):
            pass

    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())
    captured = {}

    async def fake_download(client, torrent, dest_root, *, folder=None, file_ids=None,
                            progress=None, byte_progress=None, cancel=None, mode=None):
        captured["file_ids"] = file_ids
        used = folder or (dest_root / "Bundle")
        used.mkdir(parents=True, exist_ok=True)
        (used / "01.mp3").write_bytes(b"")
        return AcquireResult(folder=used, files=[AcquiredFile("01.mp3", used / "01.mp3", True)])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    await ctrl.rd_download("tid", name="Bundle", file_ids=[2, 3])
    assert captured["file_ids"] == {2, 3}                 # set, threaded through
    assert ctrl.active_downloads()[0].file_ids == [2, 3]  # stored on the entry
    ctx.close()


async def test_rd_download_uses_given_dest_dir_and_tracks_file_counts(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdTorrentInfo
    from colophon.services.acquire import AcquiredFile, AcquireResult

    ctx = _ctx(tmp_path)
    ctx.config.real_debrid_token = "t"
    ctx.config.real_debrid_download_dir = tmp_path / "default"
    ctrl = AppController(ctx)

    class FakeClient:
        async def torrent_info(self, tid):
            return RdTorrentInfo(id=tid, filename="Bk", status="downloaded", links=["L1", "L2"])
        async def aclose(self):
            pass

    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())
    captured = {}

    async def fake_download(client, torrent, dest_root, *, folder=None, file_ids=None,
                            progress=None, byte_progress=None, cancel=None, mode=None):
        captured["dest_root"] = dest_root
        used = folder or (dest_root / "Bk")
        used.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(1, 2, "01.mp3")
            progress(2, 2, "02.mp3")   # drives the queue-count fields on the entry
        (used / "01.mp3").write_bytes(b"")
        return AcquireResult(folder=used, files=[AcquiredFile("01.mp3", used / "01.mp3", True)])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    custom = tmp_path / "chosen"
    await ctrl.rd_download("tid", name="Bk", dest_dir=custom)
    assert captured["dest_root"] == custom               # override honored, not the default dir
    entry = ctrl.active_downloads()[0]
    assert entry.files_total == 2 and entry.files_done == 2
    ctx.close()


def test_cancel_download_discards_partials_and_entry(tmp_path):
    from colophon.controller import DownloadEntry

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    folder = tmp_path / "dl"
    folder.mkdir()
    (folder / "01.mp3.part").write_bytes(b"x")   # a retained partial
    ctrl._downloads["T"] = DownloadEntry(key="T", name="Bk", status="paused")
    ctrl._download_folders["T"] = folder

    ctrl.cancel_download("T")
    assert "T" not in ctrl._downloads              # entry dropped
    assert not (folder / "01.mp3.part").exists()   # partial deleted
    assert not folder.exists()                     # emptied container removed
    ctx.close()


def test_cancel_download_keeps_other_files_in_a_shared_folder(tmp_path):
    from colophon.controller import DownloadEntry

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book_folder = tmp_path / "book"
    book_folder.mkdir()
    (book_folder / "keep.m4b").write_bytes(b"good")   # a pre-existing file (book-folder fix)
    (book_folder / "01.mp3.part").write_bytes(b"x")   # our partial
    ctrl._downloads["T"] = DownloadEntry(key="T", name="Bk", status="active")
    ctrl._download_folders["T"] = book_folder

    ctrl.cancel_download("T")
    assert not (book_folder / "01.mp3.part").exists()  # partial gone
    assert (book_folder / "keep.m4b").exists()         # other files untouched
    assert book_folder.exists()                        # non-empty folder kept
    ctx.close()


async def test_resume_download_reapplies_file_ids(tmp_path, monkeypatch):
    from colophon.adapters.realdebrid import RdTorrentInfo
    from colophon.services.acquire import AcquiredFile, AcquireResult

    ctx = _ctx(tmp_path)
    ctx.config.real_debrid_token = "t"
    ctx.config.real_debrid_download_dir = tmp_path / "dl"
    ctrl = AppController(ctx)

    class FakeClient:
        async def torrent_info(self, tid):
            return RdTorrentInfo(id=tid, filename="Bundle", status="downloaded", links=["L1"])
        async def aclose(self):
            pass

    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())
    seen = []

    async def fake_download(client, torrent, dest_root, *, folder=None, file_ids=None,
                            progress=None, byte_progress=None, cancel=None, mode=None):
        seen.append(file_ids)
        used = folder or (dest_root / "Bundle")
        used.mkdir(parents=True, exist_ok=True)
        (used / "01.mp3").write_bytes(b"")
        return AcquireResult(folder=used, files=[AcquiredFile("01.mp3", used / "01.mp3", True)])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    await ctrl.rd_download("tid", name="Bundle", file_ids=[5])
    await ctrl.resume_download("tid")
    assert seen == [{5}, {5}]  # resume re-applies the stored subset
    ctx.close()


async def test_quick_match_scan_picks_best_and_carries_confidence(tmp_path):
    src = _StubSource("audnexus", [
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="audnexus", title="Dune Messiah", authors=["Frank Herbert"]),
    ])
    ctx = _ctx(tmp_path, sources=[src])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    proposals = await AppController(ctx).quick_match_scan([book], ["audnexus"])
    assert len(proposals) == 1
    assert proposals[0].best.title == "Dune"          # best ranked first
    assert proposals[0].confidence > 0
    assert len(proposals[0].results) == 2             # full results carried
    ctx.close()


async def test_quick_match_scan_filters_sources_by_name(tmp_path):
    a = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])])
    g = _StubSource("google", [SourceResult(provider="google", title="WRONG")])
    ctx = _ctx(tmp_path, sources=[a, g])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    proposals = await AppController(ctx).quick_match_scan([book], ["audnexus"])  # google excluded
    providers = {r.provider for r in proposals[0].results}
    assert providers == {"audnexus"}
    ctx.close()


async def test_quick_match_scan_no_results_yields_none_best(tmp_path):
    ctx = _ctx(tmp_path, sources=[_StubSource("audnexus", [])])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Unknown"
    ctx.books.upsert(book)
    proposals = await AppController(ctx).quick_match_scan([book], ["audnexus"])
    assert proposals[0].best is None
    ctx.close()


async def test_quick_match_apply_overwrites_and_sets_ready(tmp_path):
    # Two sources agreeing on title+author pushes confidence over the default 75 threshold.
    a = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE")])
    g = _StubSource("google", [SourceResult(provider="google", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[a, g])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "dune"          # lowercase, will be overwritten
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    proposals = await ctrl.quick_match_scan([book], ["audnexus", "google"])
    summary = ctrl.quick_match_apply(proposals)
    persisted = ctx.books.get(book.id)
    assert persisted.title == "Dune"                       # overwritten from match
    assert persisted.asin == "B002V1A0WE"                  # filled
    assert persisted.provenance["title"] == "audnexus"     # provider of best result
    assert persisted.state == BookState.READY              # re-scored over threshold
    assert summary.applied_count == 1
    assert summary.now_ready_count == 1
    ctx.close()


async def test_quick_match_apply_low_confidence_stays_needs_review(tmp_path):
    src = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[src])  # single source -> below default threshold
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    proposals = await ctrl.quick_match_scan([book], ["audnexus"])
    summary = ctrl.quick_match_apply(proposals)
    assert ctx.books.get(book.id).state == BookState.NEEDS_REVIEW
    assert summary.now_ready_count == 0
    ctx.close()


async def test_quick_match_apply_undo_reverts_fields(tmp_path):
    a = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])])
    g = _StubSource("google", [SourceResult(provider="google", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[a, g])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    proposals = await ctrl.quick_match_scan([book], ["audnexus", "google"])
    summary = ctrl.quick_match_apply(proposals)
    ctrl.undo(summary.batch_id)
    assert ctx.books.get(book.id).title == "dune"   # field reverted
    ctx.close()


async def test_quick_match_apply_skips_proposals_without_best(tmp_path):
    ctx = _ctx(tmp_path, sources=[_StubSource("audnexus", [])])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "Unknown"
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    proposals = await ctrl.quick_match_scan([book], ["audnexus"])
    summary = ctrl.quick_match_apply(proposals)
    assert summary.applied_count == 0
    ctx.close()


def test_known_genres_and_tags_distinct_sorted(tmp_path):
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.genres = ["Fantasy", "Epic"]
    a.tags = ["gift"]
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.genres = ["Fantasy"]
    b.tags = ["to-relisten", "gift"]
    for x in (a, b):
        ctx.books.upsert(x)
    ctrl = AppController(ctx)
    assert ctrl.known_genres() == ["Epic", "Fantasy"]
    assert ctrl.known_tags() == ["gift", "to-relisten"]
    ctx.close()


def test_match_field_values_includes_genres_tags():
    r = SourceResult(provider="audnexus", genres=["Fantasy"], tags=["Epic"])
    updates = AppController.match_field_values(r)
    assert updates["genre"] == "Fantasy"
    assert updates["tag"] == "Epic"


def test_match_field_values_omits_genres_tags_when_absent():
    r = SourceResult(provider="audnexus", title="Dune")
    updates = AppController.match_field_values(r)
    assert "genre" not in updates
    assert "tag" not in updates


def test_match_field_values_includes_isbn():
    r = SourceResult(provider="openlibrary", isbn="9780306406157")
    assert AppController.match_field_values(r)["isbn"] == "9780306406157"
    assert "isbn" not in AppController.match_field_values(SourceResult(provider="x", title="T"))


def test_match_field_values_includes_publisher_and_language():
    r = SourceResult(provider="x", publisher="Tor Books", language="English")
    updates = AppController.match_field_values(r)
    assert updates["publisher"] == "Tor Books"
    assert updates["language"] == "English"
    bare = AppController.match_field_values(SourceResult(provider="x", title="T"))
    assert "publisher" not in bare and "language" not in bare


def test_apply_match_merges_genres_and_tags(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.genres = ["Fantasy", "My Custom"]
    book.tags = ["mine"]
    ctx.books.upsert(book)
    result = SourceResult(
        provider="audnexus", genres=["Fantasy", "Epic"], tags=["mine", "audible-tag"]
    )
    ctrl = AppController(ctx)
    ctrl.apply_match_fields(book, result, {"genre", "tag"})
    p = ctx.books.get(book.id)
    assert p.genres == ["Fantasy", "My Custom", "Epic"]
    assert p.tags == ["mine", "audible-tag"]
    assert p.provenance["genres"] == "audnexus"
    ctx.close()


def test_source_label_maps_audnexus_to_audible(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    assert ctrl.source_label("audnexus") == "Audible"
    assert ctrl.source_label("manual") == "Edited"          # local tier label
    assert ctrl.source_label("googlebooks") == "Google Books"
    ctx.close()


def test_source_label_and_tooltip_for_local_tiers(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    assert ctrl.source_label("graphing") == "Inferred"
    assert ctrl.source_tooltip("graphing") == (
        "Inferred from the author folder (a nearby tagged book named the author)."
    )
    # a match source falls through to its source label + a "Matched from" tooltip
    assert ctrl.source_label("audnexus") == "Audible"
    assert ctrl.source_tooltip("audnexus") == "Matched from Audible"
    ctx.close()


def test_bulk_normalize_genre_applies_policy(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.genre_mapping = {"scifi": "Science Fiction"}
    ctx.config.accepted_genres = ["Science Fiction"]
    ctx.config.genre_whitelist_enabled = True
    book = BookUnit.new(source_folder=tmp_path / "a")
    book.genres = ["scifi", "Dragons"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    ctrl.bulk_normalize([book], ["genre"])
    p = ctx.books.get(book.id)
    assert p.genres == ["Science Fiction"]
    ctx.close()


def test_bulk_normalize_genre_no_filter_when_disabled(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.genre_mapping = {"scifi": "Science Fiction"}
    ctx.config.genre_whitelist_enabled = False
    book = BookUnit.new(source_folder=tmp_path / "a")
    book.genres = ["scifi", "Dragons"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    ctrl.bulk_normalize([book], ["genre"])
    p = ctx.books.get(book.id)
    assert p.genres == ["Science Fiction", "Dragons"]
    ctx.close()


def test_genre_policy_reflects_config(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.genre_mapping = {"scifi": "Science Fiction"}
    ctx.config.accepted_genres = ["Science Fiction"]
    ctx.config.genre_whitelist_enabled = True
    pol = AppController(ctx).genre_policy()
    assert pol.canonicalize(["scifi", "Dragons"]) == ["Science Fiction"]
    ctx.close()


def test_apply_match_gates_incoming_genres_keeps_existing(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.accepted_genres = ["Science Fiction"]
    ctx.config.genre_whitelist_enabled = True
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.genres = ["My Custom"]
    ctx.books.upsert(book)
    result = SourceResult(provider="audnexus", genres=["Science Fiction", "Dragons"])
    ctrl = AppController(ctx)
    ctrl.apply_match_fields(book, result, {"genre"})
    p = ctx.books.get(book.id)
    assert p.genres == ["My Custom", "Science Fiction"]
    ctx.close()


async def test_quick_match_apply_merges_genres_tags(tmp_path):
    a = _StubSource("audnexus", [SourceResult(
        provider="audnexus", title="Dune", authors=["Frank Herbert"],
        genres=["Fantasy", "Epic"], tags=["from-audible"],
    )])
    g = _StubSource("google", [SourceResult(provider="google", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[a, g])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.genres = ["My Custom"]
    book.tags = ["mine"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    proposals = await ctrl.quick_match_scan([book], ["audnexus", "google"])
    ctrl.quick_match_apply(proposals)
    p = ctx.books.get(book.id)
    assert p.genres == ["My Custom", "Fantasy", "Epic"]  # merged, existing first
    assert p.tags == ["mine", "from-audible"]
    ctx.close()


class _ChapterStub:
    name = "audnexus"

    def __init__(self, fetch):
        self._fetch = fetch

    async def search(self, query):
        return []

    async def fetch_chapters(self, asin):
        return self._fetch


async def test_apply_audnexus_chapters_sets_and_flags_mismatch(tmp_path):
    from colophon.adapters.sources.audnexus import ChapterFetch
    from colophon.core.models import Chapter, SourceFile
    chs = [Chapter(title="Intro", start_ms=0, end_ms=120_000)]
    ctx = _ctx(tmp_path, sources=[_ChapterStub(ChapterFetch(chapters=chs, runtime_ms=3_600_000))])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.asin = "B00X"
    book.source_files = [SourceFile(path=tmp_path / "a.mp3", size=1, duration_seconds=100.0, ext="mp3")]
    ctx.books.upsert(book)
    res = await AppController(ctx).apply_audnexus_chapters(book)
    assert res.ok and res.count == 1 and res.mismatch is True
    assert res.audible_runtime_ms == 3_600_000 and res.source_runtime_ms == 100_000
    assert ctx.books.get(book.id).chapters[0].title == "Intro"
    ctx.close()


async def test_apply_audnexus_chapters_within_tolerance_no_mismatch(tmp_path):
    from colophon.adapters.sources.audnexus import ChapterFetch
    from colophon.core.models import Chapter, SourceFile
    chs = [Chapter(title="Intro", start_ms=0, end_ms=100_000)]
    ctx = _ctx(tmp_path, sources=[_ChapterStub(ChapterFetch(chapters=chs, runtime_ms=100_000))])
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.asin = "B00X"
    book.source_files = [SourceFile(path=tmp_path / "a.mp3", size=1, duration_seconds=100.0, ext="mp3")]
    ctx.books.upsert(book)
    res = await AppController(ctx).apply_audnexus_chapters(book)
    assert res.ok and res.mismatch is False
    ctx.close()


async def test_apply_audnexus_chapters_no_asin_errors(tmp_path):
    from colophon.adapters.sources.audnexus import ChapterFetch
    ctx = _ctx(tmp_path, sources=[_ChapterStub(ChapterFetch())])
    book = BookUnit.new(source_folder=tmp_path / "x")
    ctx.books.upsert(book)
    res = await AppController(ctx).apply_audnexus_chapters(book)
    assert res.ok is False and res.error
    ctx.close()


async def test_apply_audnexus_chapters_none_fetch_errors(tmp_path):
    ctx = _ctx(tmp_path, sources=[_ChapterStub(None)])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.asin = "B00X"
    ctx.books.upsert(book)
    res = await AppController(ctx).apply_audnexus_chapters(book)
    assert res.ok is False
    ctx.close()


def test_reset_chapters_clears(tmp_path):
    from colophon.core.models import Chapter
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.chapters = [Chapter(title="Intro", start_ms=0, end_ms=1000)]
    ctx.books.upsert(book)
    AppController(ctx).reset_chapters(book)
    assert ctx.books.get(book.id).chapters == []
    ctx.close()


def test_process_one_passes_stored_chapters(tmp_path, monkeypatch):
    from colophon.core.models import Chapter, SourceFile
    from colophon.services.encode import EncodeResult
    captured = {}

    def fake_encode(book, output_path, **kwargs):
        captured["chapters"] = kwargs.get("chapters")
        return EncodeResult(book_id=book.id, error="stop")

    monkeypatch.setattr("colophon.controller.encode_book", fake_encode)
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.source_files = [SourceFile(path=tmp_path / "a.mp3", size=1, duration_seconds=1.0, ext="mp3")]
    book.chapters = [Chapter(title="Intro", start_ms=0, end_ms=1000)]
    ctx.books.upsert(book)
    AppController(ctx).process_one(book)
    assert captured["chapters"] == book.chapters
    ctx.close()


def test_process_one_no_chapters_passes_none(tmp_path, monkeypatch):
    from colophon.core.models import SourceFile
    from colophon.services.encode import EncodeResult
    captured = {}

    def fake_encode(book, output_path, **kwargs):
        captured["chapters"] = kwargs.get("chapters")
        return EncodeResult(book_id=book.id, error="stop")

    monkeypatch.setattr("colophon.controller.encode_book", fake_encode)
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.source_files = [SourceFile(path=tmp_path / "a.mp3", size=1, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)
    AppController(ctx).process_one(book)
    assert captured["chapters"] is None
    ctx.close()


_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngbody"
_JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegbody"


def test_set_cover_url_sets_and_clears_cached_path(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.cover_path = tmp_path / "old.jpg"
    ctx.books.upsert(book)
    AppController(ctx).set_cover_url(book, "http://example/cover.jpg")
    p = ctx.books.get(book.id)
    assert p.cover_url == "http://example/cover.jpg"
    assert p.cover_path is None
    ctx.close()


def test_set_cover_upload_png_writes_file(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    book.cover_url = "http://old"
    ctx.books.upsert(book)
    res = AppController(ctx).set_cover_upload(book, _PNG, "art.png")
    assert res.ok
    p = ctx.books.get(book.id)
    assert p.cover_path == book.source_folder / f"cover-{book.id}.png"  # per-book, collision-safe
    assert p.cover_path.read_bytes() == _PNG
    assert p.cover_url is None
    ctx.close()


def test_set_cover_upload_jpeg_extension(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    ctx.books.upsert(book)
    res = AppController(ctx).set_cover_upload(book, _JPEG, "art.jpg")
    assert res.ok
    assert ctx.books.get(book.id).cover_path == book.source_folder / f"cover-{book.id}.jpg"
    ctx.close()


def test_set_cover_upload_rejects_non_image(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    book.source_folder.mkdir(parents=True)
    ctx.books.upsert(book)
    res = AppController(ctx).set_cover_upload(book, b"not an image", "x.txt")
    assert res.ok is False and res.error
    assert ctx.books.get(book.id).cover_path is None
    ctx.close()


async def test_cover_candidates_distinct_in_order(tmp_path):
    src = _StubSource("audnexus", [
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], cover_url="http://a/1.jpg"),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], cover_url="http://a/1.jpg"),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], cover_url="http://a/2.jpg"),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
    ])
    ctx = _ctx(tmp_path, sources=[src])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    cands = await AppController(ctx).cover_candidates(book)
    assert cands == ["http://a/1.jpg", "http://a/2.jpg"]
    ctx.close()


def test_clear_cover_clears_both(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.cover_url = "http://u"
    book.cover_path = tmp_path / "c.jpg"
    ctx.books.upsert(book)
    AppController(ctx).clear_cover(book)
    p = ctx.books.get(book.id)
    assert p.cover_url is None and p.cover_path is None
    ctx.close()


async def test_ensure_cover_cached_noop_when_already_cached(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.source_folder.mkdir(parents=True)
    cp = book.source_folder / "cover.jpg"
    cp.write_bytes(b"img")
    book.cover_path = cp
    ctx.books.upsert(book)
    calls = {"n": 0}

    async def fake_ensure(*args, **kwargs):
        calls["n"] += 1
        return None

    monkeypatch.setattr("colophon.controller.ensure_cached_cover", fake_ensure)
    await AppController(ctx).ensure_cover_cached(book)
    assert calls["n"] == 0
    ctx.close()


async def test_ensure_cover_cached_fetches_and_persists(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.source_folder.mkdir(parents=True)
    book.cover_url = "http://example/c.jpg"
    ctx.books.upsert(book)
    cached = book.source_folder / "cover.jpg"

    async def fake_ensure(b, *, dest_dir, client=None):
        cached.write_bytes(b"img")
        b.cover_path = cached
        return cached

    monkeypatch.setattr("colophon.controller.ensure_cached_cover", fake_ensure)
    await AppController(ctx).ensure_cover_cached(book)
    assert ctx.books.get(book.id).cover_path == cached
    ctx.close()


def test_quick_match_scan_passes_search_fields(tmp_path):
    src = _RecordingSource("cap", [SourceResult(provider="cap", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[src])
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "Dune"
    b.authors = ["Wrong Author"]
    b.asin = "B002V1A0WE"
    ctx.books.upsert(b)
    asyncio.run(AppController(ctx).quick_match_scan([b], ["cap"], {"title"}))
    assert len(src.queries) == 1
    q = src.queries[0]
    assert q.title == "Dune"
    assert q.author is None
    assert q.asin is None
    ctx.close()


def test_normalize_match_updates_only_configured_fields(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.normalize_on_match = ["title"]
    ctrl = AppController(ctx)
    updates = {"title": "the lost   metal", "author": "brandon sanderson"}
    ctrl._normalize_match_updates(updates)
    assert updates["title"] == "The Lost Metal"
    assert updates["author"] == "brandon sanderson"  # not configured: untouched
    ctx.close()


def test_normalize_match_updates_ignores_unknown_and_empty(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.normalize_on_match = ["bogus", "title"]
    ctrl = AppController(ctx)
    updates = {"title": None}
    ctrl._normalize_match_updates(updates)
    assert updates["title"] is None
    ctx.close()


def test_apply_match_fields_normalizes_configured_field(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.normalize_on_match = ["title"]
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    ctx.books.upsert(b)
    result = SourceResult(provider="stub", title="the lost   metal")
    ctrl.apply_match_fields(b, result, {"title"})
    assert ctx.books.get(b.id).title == "The Lost Metal"
    ctx.close()


def test_quick_match_apply_normalizes_configured_field(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.normalize_on_match = ["title"]
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    ctx.books.upsert(b)
    from colophon.core.quickmatch import QuickMatchProposal

    best = SourceResult(provider="stub", title="the lost   metal", authors=["Brandon Sanderson"])
    proposal = QuickMatchProposal(book=b, best=best, results=[best], confidence=90.0)
    ctrl.quick_match_apply([proposal])
    assert ctx.books.get(b.id).title == "The Lost Metal"
    ctx.close()


def test_normalize_on_match_config_round_trip(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config

    path = tmp_path / "config.toml"
    save_config(Config(normalize_on_match=["title", "description"]), path)
    assert load_config(path).normalize_on_match == ["title", "description"]


def test_catalog_entries_and_rename(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.authors = ["JRR Tolkien"]
    ctx.books.upsert(b)
    assert any(e.name == "JRR Tolkien" and e.count == 1 for e in ctrl.catalog_entries("author"))
    res = ctrl.rename_catalog_entry("author", "JRR Tolkien", "J.R.R. Tolkien")
    assert res.affected_count == 1
    assert res.batch_id
    assert ctx.books.get(b.id).authors == ["J.R.R. Tolkien"]
    ctx.close()


def test_catalog_merge_and_delete(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.authors = ["JRR Tolkien"]
    ctx.books.upsert(a)
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.authors = ["J.R.R Tolkien"]
    ctx.books.upsert(b)
    res = ctrl.merge_catalog_entries("author", ["JRR Tolkien", "J.R.R Tolkien"], "J.R.R. Tolkien")
    assert res.affected_count == 2
    assert ctx.books.get(a.id).authors == ["J.R.R. Tolkien"]
    assert ctx.books.get(b.id).authors == ["J.R.R. Tolkien"]
    res2 = ctrl.delete_catalog_entry("author", "J.R.R. Tolkien")
    assert res2.affected_count == 2
    assert ctx.books.get(a.id).authors == []
    ctx.close()


def test_match_field_values_includes_subtitle():
    from colophon.controller import AppController
    from colophon.core.sources import SourceResult
    updates = AppController.match_field_values(
        SourceResult(provider="audnexus", title="PHM", subtitle="A Novel")
    )
    assert updates["subtitle"] == "A Novel"
    assert "subtitle" not in AppController.match_field_values(
        SourceResult(provider="audnexus", title="PHM")
    )


def test_catalog_result_reports_affected_ids(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.publisher = "Tor"
    ctx.books.upsert(a)
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.publisher = "Macmillan"
    ctx.books.upsert(b)
    res = ctrl.rename_catalog_entry("publisher", "Tor", "Tor Books")
    assert res.affected_ids == [a.id]
    assert ctx.books.get(a.id).publisher == "Tor Books"
    ctx.close()

def test_apply_match_fields_rescores_confidence(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "old title"
    b.authors = ["old"]
    ctx.books.upsert(b)
    result = SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])
    ctrl.apply_match_fields(b, result, {"title", "author"})
    saved = ctx.books.get(b.id)
    assert saved.confidence > 0
    assert saved.confidence_signals  # signals recorded, not empty
    ctx.close()


def test_confirm_confidence_sets_100_ready_and_manual_flag(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    AppController(ctx).confirm_confidence(book)
    assert book.confidence == 100.0
    assert book.manually_confirmed is True
    assert book.state == BookState.READY
    assert any(s.name == "manual_confirmation" for s in book.confidence_signals)
    assert ctx.books.get(book.id).manually_confirmed is True
    ctx.close()


def test_mark_ready_forces_max_confidence_not_weak_value(tmp_path):
    # A human approving a book is a manual confirmation: mark_ready must not leave the
    # weak pre-match identity_confidence in the featured score.
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.identity_confidence = 42.0        # weak local guess before review
    ctx.books.upsert(book)
    AppController(ctx).mark_ready(book)
    assert book.confidence == 100.0
    assert book.manually_confirmed is True
    assert book.state == BookState.READY
    assert any(s.name == "manual_confirmation" for s in book.confidence_signals)
    assert ctx.books.get(book.id).confidence == 100.0
    ctx.close()


async def test_recheck_confidence_reverts_to_auto_and_clears_flag(tmp_path):
    src = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[src])
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    ctrl = AppController(ctx)
    ctrl.confirm_confidence(book)
    assert book.manually_confirmed is True
    await ctrl.recheck_confidence(book)
    assert book.manually_confirmed is False
    assert not any(s.name == "manual_confirmation" for s in book.confidence_signals)
    ctx.close()


def test_rescore_after_match_clears_manual_flag(tmp_path):
    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctrl = AppController(ctx)
    ctrl.confirm_confidence(book)
    assert book.manually_confirmed is True
    ctrl._rescore_after_match(book, [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])])
    assert book.manually_confirmed is False
    ctx.close()


def test_scan_preview_does_not_write_then_apply_persists(tmp_path):
    ingest = _seed_ingest(tmp_path)
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    plan = ctrl.scan_preview([ingest])
    assert plan.new_books == 1
    assert ctx.books.list_all() == []          # preview wrote nothing
    written = ctrl.apply_scan(plan)
    assert written == 1
    assert len(ctx.books.list_all()) == 1
    ctx.close()


async def test_identify_preview_partitions_without_writing(tmp_path):
    src = _StubSource([SourceResult(provider="stub", title="Dune", authors=["Frank Herbert"], asin="B0DUNE")])
    ctx = _ctx(tmp_path, sources=[src])
    strong = BookUnit.new(source_folder=tmp_path / "dune")
    strong.title, strong.authors, strong.asin = "Dune", ["Frank Herbert"], "B0DUNE"
    weak = BookUnit.new(source_folder=tmp_path / "misc")
    weak.title, weak.authors = "Totally Different", ["Nobody"]
    confirmed = BookUnit.new(source_folder=tmp_path / "conf")
    confirmed.title, confirmed.manually_confirmed = "X", True
    organized = BookUnit.new(source_folder=tmp_path / "org")
    organized.title, organized.output_path = "Y", tmp_path / "y.m4b"
    for b in (strong, weak, confirmed, organized):
        ctx.books.upsert(b)

    plan = await AppController(ctx).identify_preview()
    assert plan.to_apply == 1
    assert plan.to_review == 1
    assert plan.skipped == 2
    assert ctx.books.get(strong.id).state == BookState.DETECTED
    ctx.close()


async def test_apply_identify_fills_empty_and_marks_ready(tmp_path):
    src = _StubSource([SourceResult(
        provider="stub", title="Dune", authors=["Frank Herbert"],
        narrators=["Scott Brick"], series_name="Dune", series_sequence=1.0, asin="B0DUNE",
    )])
    ctx = _ctx(tmp_path, sources=[src])
    b = BookUnit.new(source_folder=tmp_path / "dune")
    b.title, b.authors, b.asin = "Dune (My Edit)", ["Frank Herbert"], "B0DUNE"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)

    plan = await ctrl.identify_preview()
    summary = ctrl.apply_identify(plan)

    out = ctx.books.get(b.id)
    assert out.state == BookState.READY
    assert out.title == "Dune (My Edit)"
    assert out.narrators == ["Scott Brick"]
    assert out.series and out.series[0].name == "Dune"
    assert summary.auto_matched == 1
    ctx.close()


async def test_apply_identify_routes_low_confidence_and_skips_confirmed(tmp_path):
    src = _StubSource([SourceResult(provider="stub", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[src])
    weak = BookUnit.new(source_folder=tmp_path / "misc")
    weak.title, weak.authors = "Totally Different", ["Nobody"]
    confirmed = BookUnit.new(source_folder=tmp_path / "conf")
    confirmed.title, confirmed.manually_confirmed, confirmed.confidence = "Keep Me", True, 100.0
    confirmed.state = BookState.READY
    ctx.books.upsert(weak)
    ctx.books.upsert(confirmed)
    ctrl = AppController(ctx)

    summary = ctrl.apply_identify(await ctrl.identify_preview())

    assert ctx.books.get(weak.id).state == BookState.NEEDS_REVIEW
    assert summary.routed_to_review == 1
    after = ctx.books.get(confirmed.id)
    assert after.manually_confirmed is True and after.title == "Keep Me" and after.confidence == 100.0
    ctx.close()


def test_source_settings_lists_enabled_then_disabled(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController

    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite",
                                   disabled_sources=["googlebooks"]))
    ctrl = AppController(ctx)
    rows = ctrl.source_settings()
    names = [name for name, _label, _enabled in rows]
    enabled = {name: en for name, _l, en in rows}
    assert "googlebooks" in names
    assert enabled["googlebooks"] is False
    assert enabled["audnexus"] is True


def test_save_settings_applies_disable(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController

    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite"), config_path=tmp_path / "c.toml"
    )
    ctrl = AppController(ctx)
    assert any(s.name == "googlebooks" for s in ctx.sources)
    ctrl.save_settings(Config(db_path=tmp_path / "db.sqlite", disabled_sources=["googlebooks"]))
    assert all(s.name != "googlebooks" for s in ctrl.ctx.sources)


def test_encode_job_types_defaults():
    from colophon.controller import CancelToken, EncodeJobOptions

    opts = EncodeJobOptions()
    assert opts.encode is True and opts.organize is True
    assert opts.delete_sources is False and opts.concurrency == 2

    tok = CancelToken()
    assert tok.cancelled is False
    tok.cancel()
    assert tok.cancelled is True


def test_process_book_encode_only_in_place_sets_encoded(tmp_path, make_audio):
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    result = AppController(ctx)._process_book(book, EncodeJobOptions(encode=True, organize=False))
    assert result.status == "done"
    persisted = ctx.books.get(book.id)
    assert persisted.state == BookState.ENCODED
    assert persisted.output_path is not None
    assert persisted.output_path.parent == a.parent  # in-place, beside the sources
    assert persisted.output_path.exists()
    ctx.close()


def test_process_book_organize_only_requires_encoded(tmp_path):
    from colophon.controller import EncodeJobOptions

    ctx = _ctx(tmp_path)
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "Dune")
    book.title = "Dune"
    book.state = BookState.READY
    ctx.books.upsert(book)  # no output_path -> nothing encoded to organize

    result = AppController(ctx)._process_book(book, EncodeJobOptions(encode=False, organize=True))
    assert result.status == "skipped"
    ctx.close()


def test_process_book_full_pipeline_tags_once(tmp_path, make_audio):
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Dune/01.mp3", seconds=1)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    result = AppController(ctx)._process_book(book, EncodeJobOptions(encode=True, organize=True))
    assert result.status == "done"
    persisted = ctx.books.get(book.id)
    assert persisted.state == BookState.ORGANIZED
    assert persisted.output_path is not None and persisted.output_path.exists()
    ops = ctx.operations.list_batch(ctx.operations.latest_batch_id())
    assert any(op.op_type == "tag_write" and op.book_id == book.id for op in ops)
    ctx.close()


async def test_run_encode_job_reports_progress_and_results(tmp_path, make_audio):
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    books = []
    for i in range(2):
        a = make_audio(f"Book{i}/01.mp3", seconds=1)
        book = BookUnit.new(source_folder=a.parent)
        book.title = f"Book {i}"
        book.authors = ["Frank Herbert"]
        book.state = BookState.READY
        book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
        ctx.books.upsert(book)
        books.append(book)

    seen: list[tuple[str, str]] = []
    result = await AppController(ctx).run_encode_job(
        books,
        EncodeJobOptions(encode=True, organize=True),
        progress=lambda bid, status: seen.append((bid, status)),
    )
    assert [r.status for r in result.results] == ["done", "done"]
    statuses = {status for _, status in seen}
    assert "encoding" in statuses
    assert "done" in statuses
    ctx.close()


async def test_run_encode_job_graceful_cancel_skips_queued(tmp_path, make_audio):
    from colophon.controller import CancelToken, EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    books = []
    for i in range(3):
        a = make_audio(f"Book{i}/01.mp3", seconds=1)
        book = BookUnit.new(source_folder=a.parent)
        book.title = f"Book {i}"
        book.authors = ["Frank Herbert"]
        book.state = BookState.READY
        book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
        ctx.books.upsert(book)
        books.append(book)

    tok = CancelToken()
    tok.cancel()
    result = await AppController(ctx).run_encode_job(
        books,
        EncodeJobOptions(encode=True, organize=False, concurrency=1),
        cancel=tok,
    )
    assert [r.status for r in result.results] == ["cancelled", "cancelled", "cancelled"]
    ctx.close()


async def test_run_encode_job_caches_cover_before_encode(tmp_path, make_audio, monkeypatch):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController, EncodeJobOptions
    from colophon.core.models import BookState, SourceFile

    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    d = tmp_path / "s"
    d.mkdir()
    a = make_audio("s/a.mp3", seconds=1)
    book = BookUnit.new(source_folder=d)
    book.title = "B"
    book.authors = ["A"]
    book.state = BookState.READY
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    ctrl = AppController(ctx)
    cached: list[str] = []

    async def _fake_cache(b):
        cached.append(b.id)

    monkeypatch.setattr(ctrl, "ensure_cover_cached", _fake_cache)
    await ctrl.run_encode_job([book], EncodeJobOptions(encode=True, organize=False))
    assert cached == [book.id]


def test_import_ll_patterns_returns_folder_only(tmp_path):
    from colophon.controller import AppController

    ini = tmp_path / "config.ini"
    ini.write_text(
        "[POSTPROCESS]\n"
        "audiobook_dest_folder = $Author/$Series/$Title\n"
        "audiobook_dest_file = $Author - $Title Part $Part of $Total\n"
    )
    assert AppController.import_ll_patterns(ini) == "$Author/$Series/$Title"


def test_import_ll_patterns_reads_folder(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[POSTPROCESS]\n"
        "audiobook_dest_folder = $Author/$Series/$Title\n"
    )
    assert AppController.import_ll_patterns(ini) == "$Author/$Series/$Title"


def test_import_ll_patterns_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        AppController.import_ll_patterns(tmp_path / "absent.ini")


def test_rd_download_registry_and_scan_prompt(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController

    dl = tmp_path / "dls"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", real_debrid_download_dir=dl),
        config_path=tmp_path / "c.toml",
    )
    ctrl = AppController(ctx)

    assert ctrl.active_downloads() == []
    assert ctrl.should_prompt_downloads_scan() is True

    ctrl.add_downloads_to_scan_paths()
    assert dl in ctrl.ctx.config.scan_paths
    assert ctrl.should_prompt_downloads_scan() is False


def test_mark_downloads_scan_prompt_seen_suppresses(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController

    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", real_debrid_download_dir=tmp_path / "dls"),
        config_path=tmp_path / "c.toml",
    )
    ctrl = AppController(ctx)
    assert ctrl.should_prompt_downloads_scan() is True
    ctrl.mark_downloads_scan_prompt_seen()
    assert ctrl.ctx.config.downloads_scan_prompt_seen is True
    assert ctrl.should_prompt_downloads_scan() is False


async def test_quick_match_scan_emits_progress_ok_and_fail(tmp_path):
    # ok: a source returns a candidate.
    ctx = _ctx(tmp_path / "ok", sources=[
        _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["x"])])
    ])
    hit = BookUnit.new(source_folder=tmp_path / "hit")
    hit.title = "Dune"
    ctx.books.upsert(hit)
    events: list[tuple[str, str]] = []
    await AppController(ctx).quick_match_scan(
        [hit], ["audnexus"], progress=lambda bid, kind: events.append((bid, kind))
    )
    assert events == [(hit.id, "ok")]
    ctx.close()

    # fail: the source returns nothing.
    ctx2 = _ctx(tmp_path / "fail", sources=[_StubSource("audnexus", [])])
    miss = BookUnit.new(source_folder=tmp_path / "miss")
    miss.title = "Nonesuch"
    ctx2.books.upsert(miss)
    events2: list[tuple[str, str]] = []
    await AppController(ctx2).quick_match_scan(
        [miss], ["audnexus"], progress=lambda bid, kind: events2.append((bid, kind))
    )
    assert events2 == [(miss.id, "fail")]
    ctx2.close()


async def test_retry_identify_requeries_only_given_ids_and_merges(tmp_path):
    # First scan: a source that returns nothing -> both books are no-match.
    ctx = _ctx(tmp_path, sources=[_StubSource("audnexus", [])])
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.title = "Dune"
    ctx.books.upsert(a)
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.title = "Hyperion"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    plan = await ctrl.identify_preview()
    assert all(p.best is None for p in plan.proposals)  # both no-match

    # Now the source can find a candidate; retry only book a.
    ctx.sources = [_StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["x"])])]
    merged = await ctrl.retry_identify(plan, [a.id])

    by_id = {p.book.id: p for p in merged.proposals}
    assert by_id[a.id].best is not None  # a re-queried and matched
    assert by_id[b.id].best is None      # b untouched
    assert len(merged.proposals) == 2
    ctx.close()


def test_organize_targets_uses_overridden_patterns(tmp_path):
    from colophon.adapters.lazylibrarian import PathPatterns
    ctx = _ctx(tmp_path)
    ctx.config.library_root = tmp_path / "lib"
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    targets = AppController(ctx).organize_targets(
        [book], patterns=PathPatterns(folder="$Author", single_file="$Title")
    )
    bid, target = targets[0]
    assert bid == book.id
    assert target == (tmp_path / "lib" / "Frank Herbert" / "Dune.m4b")
    ctx.close()


async def test_scan_preview_honors_template_override(tmp_path, monkeypatch):
    import colophon.controller as ctrl_mod
    from colophon.services.ingest import ScanPlan
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [tmp_path]
    captured = {}

    def fake_plan_scan(repo, root, *, template, directory_scheme="", options=None,
                       inference_root=None, progress=None, node_overrides=None,
                       known_franchises=None, single_book_folders=frozenset()):
        captured["template"] = template
        captured["scheme"] = directory_scheme
        return ScanPlan()

    monkeypatch.setattr(ctrl_mod, "plan_scan_graph", fake_plan_scan)
    AppController(ctx).scan_preview(template="$Title", directory_scheme="$Author")
    assert captured["template"] == "$Title"
    assert captured["scheme"] == "$Author"
    ctx.close()


def test_pattern_history_record_dedup_cap_and_remove(tmp_path):
    from colophon.adapters.config import OrganizePattern, load_config
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    ctrl.record_filename_template("$Author - $Title")
    ctrl.record_filename_template("$Title")
    ctrl.record_filename_template("$Author - $Title")  # re-use -> front, no dup
    assert ctx.config.recent_filename_templates == ["$Author - $Title", "$Title"]

    ctrl.record_filename_template("   ")  # blank ignored
    assert ctx.config.recent_filename_templates == ["$Author - $Title", "$Title"]

    for i in range(12):
        ctrl.record_directory_scheme(f"$Author/$Series{i}")
    assert len(ctx.config.recent_directory_schemes) == 10  # capped

    ctrl.record_organize_pattern("$Author", "$Title")
    assert ctx.config.recent_organize_patterns == [OrganizePattern(folder="$Author", file="$Title")]
    ctrl.remove_organize_pattern("$Author", "$Title")
    assert ctx.config.recent_organize_patterns == []

    ctrl.remove_filename_template("$Title")
    assert ctx.config.recent_filename_templates == ["$Author - $Title"]
    ctrl.clear_pattern_history()
    assert ctx.config.recent_filename_templates == [] and ctx.config.recent_directory_schemes == []

    assert load_config(ctx.config_path).recent_filename_templates == []  # persisted
    ctx.close()


def test_quick_match_scan_caps_concurrency(tmp_path):
    state = {"cur": 0, "max": 0}

    class _SlowSource:
        name = "audnexus"

        async def search(self, query):
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
            await asyncio.sleep(0.01)
            state["cur"] -= 1
            return [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])]

    ctx = _ctx(tmp_path, sources=[_SlowSource()])
    ctrl = AppController(ctx)
    books = [BookUnit.new(source_folder=tmp_path / f"b{i}") for i in range(20)]
    for b in books:
        b.title = "Dune"
    proposals = asyncio.run(ctrl.quick_match_scan(books, ["audnexus"]))
    assert len(proposals) == 20
    assert state["max"] <= 8
    assert state["max"] > 1


def test_identify_candidates_excludes_unmatchable(tmp_path):
    from colophon.core.models import Phase, PhaseState
    from colophon.core.phases import mark

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    plain = BookUnit.new(source_folder=tmp_path / "plain")
    plain.title = "Dune"

    no_title = BookUnit.new(source_folder=tmp_path / "notitle")

    matched = BookUnit.new(source_folder=tmp_path / "matched")
    matched.title = "Matched"
    mark(matched, Phase.MATCH, PhaseState.FRESH)

    confirmed = BookUnit.new(source_folder=tmp_path / "conf")
    confirmed.title = "Confirmed"
    confirmed.manually_confirmed = True

    for b in (plain, no_title, matched, confirmed):
        ctx.books.upsert(b)

    assert [b.id for b in ctrl.identify_candidates()] == [plain.id]


def test_author_inferred_flag_blocks_ready(tmp_path):
    from colophon.core.quickmatch import QuickMatchProposal

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=tmp_path / "b")
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ctx.books.upsert(book)
    results = [
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
    ]
    flagged = QuickMatchProposal(
        book=book, best=results[0], results=results, confidence=99.0, author_inferred=True)
    plain = QuickMatchProposal(
        book=book, best=results[0], results=results, confidence=99.0, author_inferred=False)

    assert ctrl._rescore_and_persist(flagged) is False
    assert ctrl._rescore_and_persist(plain) is True


def test_identify_plan_routes_inferred_to_review(tmp_path):
    from colophon.core.quickmatch import QuickMatchProposal

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=tmp_path / "b")
    book.title = "The Gunslinger"
    best = SourceResult(provider="audnexus", title="The Gunslinger", authors=["Stephen King"])
    p = QuickMatchProposal(book=book, best=best, results=[best], confidence=99.0, author_inferred=True)
    plan = ctrl._identify_plan([p], skipped=0)
    assert plan.to_apply == 0 and plan.to_review == 1


def test_apply_identify_does_not_fill_inferred_author(tmp_path):
    from colophon.core.quickmatch import QuickMatchProposal

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = BookUnit.new(source_folder=tmp_path / "b")
    book.title = "The Gunslinger"
    ctx.books.upsert(book)
    best = SourceResult(provider="audnexus", title="The Gunslinger", authors=["Stephen King"])
    p = QuickMatchProposal(book=book, best=best, results=[best], confidence=99.0, author_inferred=True)

    ctrl.apply_identify(ctrl._identify_plan([p], skipped=0))
    assert ctx.books.get(book.id).authors == []


def test_graph_roots_returns_configured_scan_paths(tmp_path):
    from pathlib import Path
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [Path("/a"), Path("/b")]
    ctrl = AppController(ctx)
    assert ctrl.graph_roots() == [Path("/a"), Path("/b")]
    ctx.close()


def test_graph_for_builds_and_resolves_author_subtree(tmp_path):
    from mutagen.id3 import ID3, TPE1

    from colophon.core.graph_view import graph_tree

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ingest = tmp_path / "ingest"
    coll = ingest / "Stephen King" / "-collection-"
    tagged = coll / "The Gunslinger"
    untagged = coll / "Wizard and Glass"
    tagged.mkdir(parents=True)
    untagged.mkdir(parents=True)
    f = tagged / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Stephen King"]))
    id3.save(f)
    (untagged / "01.mp3").write_bytes(b"")

    # graph_for must run the resolution pass, so the AUTHOR classification appears.
    top = graph_tree(ctrl.graph_for(ingest), ingest)
    sk = top[0]
    assert sk.label == "Stephen King"
    assert sk.badges[0].startswith("AUTHOR → Stephen King · ")   # auto -> name + confidence
    coll_node = sk.children[0]
    book_titles = {
        d.children[0].label
        for d in coll_node.children
        if d.children and d.children[0].node_kind == "book"
    }
    assert {"The Gunslinger", "Wizard and Glass"} <= book_titles
    ctx.close()


def test_scan_preview_forwards_progress(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")
    (ingest / "Legion").mkdir(parents=True)
    (ingest / "Legion" / "01.mp3").write_bytes(b"")
    ctx.config.scan_paths = [ingest]

    calls: list[tuple[int, int, str]] = []
    ctrl.scan_preview(progress=lambda d, t, label: calls.append((d, t, label)))
    labels = {label for _, _, label in calls}
    assert {"Dune", "Legion"} <= labels                              # folder walk
    assert {"Identifying: Dune", "Identifying: Legion"} <= labels    # per-book identify phase
    ctx.close()


async def test_scan_preview_streamed_returns_plan_and_emits_progress(tmp_path):
    import asyncio
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")
    ctx.config.scan_paths = [ingest]

    calls: list[tuple[int, int, str]] = []
    plan = await ctrl.scan_preview_streamed(progress=lambda d, t, label: calls.append((d, t, label)))
    await asyncio.sleep(0)  # let the call_soon_threadsafe callbacks run
    assert plan.new_books == 1
    assert calls and calls[-1][1] == 1   # total == 1 folder
    ctx.close()


def test_graph_for_caches_and_cached_graph_returns_it(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")

    assert ctrl.cached_graph(ingest) is None              # nothing built yet
    built = ctrl.graph_for(ingest)
    assert ctrl.cached_graph(ingest) is built             # same object, cached
    assert ctrl.cached_graph(tmp_path / "other") is None  # a different, unbuilt root
    ctx.close()


def test_graph_for_fresh_ignores_persisted_state(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    book = ctx.books.list_all()[0]
    book.title = "POISONED"
    book.provenance["title"] = "tag"  # a persisted embedded-tag title (survives a normal re-identify)
    ctx.books.upsert(book)

    normal = ctrl.graph_for(ingest)
    fresh = ctrl.graph_for(ingest, fresh=True)

    assert [bn.book.title for bn in normal.books.values()] == ["POISONED"]
    assert [bn.book.title for bn in fresh.books.values()] == ["Dune"]  # disk-derived
    ctx.close()


def test_graph_for_caches_modes_separately(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    g_normal = ctrl.graph_for(ingest, fresh=False)
    g_fresh = ctrl.graph_for(ingest, fresh=True)

    assert g_normal is not g_fresh
    assert ctrl.cached_graph(ingest, fresh=False) is g_normal
    assert ctrl.cached_graph(ingest, fresh=True) is g_fresh
    ctx.close()


async def test_graph_for_streamed_returns_graph_and_emits_progress(tmp_path):
    import asyncio
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    calls: list[tuple[int, int, str]] = []
    graph = await ctrl.graph_for_streamed(
        ingest, progress=lambda d, t, label: calls.append((d, t, label)))
    await asyncio.sleep(0)  # let the call_soon_threadsafe callbacks run

    assert [bn.book.title for bn in graph.books.values()] == ["Dune"]
    assert calls and calls[-1][1] == 1  # total == 1 folder
    ctx.close()


def test_graph_for_runs_coarse_classification(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode

    graph = ctrl.graph_for(ingest)
    dune = graph.directories[DirectoryNode.id_for(ingest / "Dune")]
    # single-book leaf -> title; the one book's tagged author is a competing (soft) author vote
    assert dune.kind == "title" and dune.kind_confidence == 0.83
    ctx.close()


def test_graph_for_resolves_grouping_to_author(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode

    graph = ctrl.graph_for(ingest)
    root = graph.directories[DirectoryNode.id_for(ingest)]
    # the engine resolves the grouping (one standalone title) straight to author, no separate hint
    assert root.kind == "author"
    assert root.kind_source == ""                # auto -> unconfirmed, eligible for the cohort
    ctx.close()


def test_node_classification_override_sticky_and_clear(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]  # so _scan_root_for_path resolves to the scanned root
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode
    dune_id = DirectoryNode.id_for(ingest / "Dune")

    g0 = ctrl.graph_for(ingest)                      # caches: Dune is auto "title"
    assert g0.directories[dune_id].kind == "title"

    ctrl.set_node_classification(ingest / "Dune", "franchise", "DOCTOR WHO")
    g1 = ctrl.graph_for(ingest)                      # cache invalidated -> override applied
    node = g1.directories[dune_id]
    assert node.kind == "franchise"
    assert node.kind_source == "manual"
    assert node.kind_value == "DOCTOR WHO"

    g2 = ctrl.graph_for(ingest)                      # sticky across rebuild
    assert g2.directories[dune_id].kind == "franchise"

    ctrl.clear_node_classification(ingest / "Dune")
    g3 = ctrl.graph_for(ingest)                      # reverts to auto
    assert g3.directories[dune_id].kind == "title"
    assert g3.directories[dune_id].kind_source == ""
    ctx.close()


def test_confirm_hint_cohort_confirms_author_groupings(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    for author in ("Author A", "Author B"):
        d = ingest / author / "Book"
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")

    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode

    n = ctrl.confirm_hint_cohort(ingest, "author")
    assert n == 2  # the two author folders; the root is excluded

    graph = ctrl.graph_for(ingest)
    for author in ("Author A", "Author B"):
        node = graph.directories[DirectoryNode.id_for(ingest / author)]
        assert node.kind == "author"
        assert node.kind_source == "manual"
        assert node.kind_value == author
    # the scan root itself is NOT confirmed
    assert graph.directories[DirectoryNode.id_for(ingest)].kind_source == ""
    ctx.close()


def test_scan_applies_author_override_to_books(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    folder = ingest / "Brandon Sanderson" / "Elantris"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"")

    ctrl = AppController(ctx)
    ctrl.set_node_classification(ingest / "Brandon Sanderson", "author", "Brandon Sanderson")
    ctrl.scan([ingest])

    book = next(b for b in ctx.books.list_all() if b.source_folder == folder)
    assert book.authors == ["Brandon Sanderson"]
    assert book.provenance["authors"] == "manual"
    ctx.close()


def test_identify_uses_confirmed_ancestor_author(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    rec = _RecordingSource()
    ctx = _ctx(tmp_path, sources=[rec])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    asyncio.run(ctrl.identify_preview())
    assert rec.queries[0].author == "Brandon Sanderson"


def test_confirmed_author_makes_proposal_not_inferred(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    s1 = _StubSource("a", [SourceResult(provider="a", title="Elantris", authors=["Brandon Sanderson"])])
    s2 = _StubSource("b", [SourceResult(provider="b", title="Elantris", authors=["Brandon Sanderson"])])
    ctx = _ctx(tmp_path, sources=[s1, s2])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    before = asyncio.run(ctrl.identify_preview())
    assert before.proposals[0].author_inferred is True
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    after = asyncio.run(ctrl.identify_preview())
    assert after.proposals[0].author_inferred is False


def test_get_matches_does_not_override_book_own_author(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Tagged"
    book_dir.mkdir(parents=True)
    rec = _RecordingSource()
    ctx = _ctx(tmp_path, sources=[rec])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Tagged"
    b.authors = ["Someone Else"]
    b.provenance["authors"] = Provenance.TAG.value
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    asyncio.run(ctrl.get_matches(b))
    assert rec.queries[0].author == "Someone Else"


def test_recheck_confidence_persists_confirmed_author(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    ctx = _ctx(tmp_path, sources=[_StubSource("a", [])])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    asyncio.run(ctrl.recheck_confidence(b))
    reloaded = ctx.books.get(b.id)
    assert reloaded.authors == ["Brandon Sanderson"]
    assert reloaded.provenance["authors"] == Provenance.MANUAL.value


def test_identify_preview_does_not_mutate_cached_book(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    ctx = _ctx(tmp_path, sources=[_RecordingSource()])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    asyncio.run(ctrl.identify_preview())
    cached = next(x for x in ctx.books.list_all() if x.id == b.id)
    assert cached.authors == []  # preview must not persist or leak the confirmed fill


def test_apply_identify_persists_confirmed_author(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    ctx = _ctx(tmp_path, sources=[_StubSource("a", [])])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    plan = asyncio.run(ctrl.identify_preview())
    ctrl.apply_identify(plan)
    reloaded = ctx.books.get(b.id)
    assert reloaded.authors == ["Brandon Sanderson"]
    assert reloaded.provenance["authors"] == Provenance.MANUAL.value


def test_retry_identify_uses_confirmed_ancestor_author(tmp_path):
    root = tmp_path / "lib"
    book_dir = root / "Brandon Sanderson" / "Elantris"
    book_dir.mkdir(parents=True)
    rec = _RecordingSource()
    ctx = _ctx(tmp_path, sources=[rec])
    ctx.config.scan_paths = [root]
    b = BookUnit.new(source_folder=book_dir)
    b.title = "Elantris"
    ctx.books.upsert(b)
    ctrl = AppController(ctx)
    plan = asyncio.run(ctrl.identify_preview())  # before any confirmation
    ctrl.set_node_classification(root / "Brandon Sanderson", "author", "Brandon Sanderson")
    rec.queries.clear()
    asyncio.run(ctrl.retry_identify(plan, [b.id]))
    assert rec.queries[0].author == "Brandon Sanderson"


# ---------------------------------------------------------------------------
# Missing-folder sweep tests
# ---------------------------------------------------------------------------

def test_sweep_marks_book_whose_folder_vanished(tmp_path):
    import shutil

    from colophon.services.ingest import sweep_missing

    root = tmp_path / "lib"
    folder = root / "Dune"
    folder.mkdir(parents=True)
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=folder)
    b.title = "Dune"
    ctx.books.upsert(b)
    shutil.rmtree(folder)
    sweep_missing(ctx.books, [root])
    assert ctx.books.get(b.id).missing is True


def test_sweep_skips_when_root_inaccessible(tmp_path):
    from colophon.services.ingest import sweep_missing

    root = tmp_path / "lib"  # never created -> not accessible
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=root / "Dune")
    ctx.books.upsert(b)
    sweep_missing(ctx.books, [root])
    assert ctx.books.get(b.id).missing is False  # unmount guard


def test_sweep_leaves_book_under_no_root_untouched(tmp_path):
    from colophon.services.ingest import sweep_missing

    root = tmp_path / "lib"
    root.mkdir()
    ctx = _ctx(tmp_path)
    # a book ingested from an ad-hoc path outside every scan root, folder gone
    b = BookUnit.new(source_folder=tmp_path / "elsewhere" / "Dune")
    ctx.books.upsert(b)
    sweep_missing(ctx.books, [root])
    assert ctx.books.get(b.id).missing is False  # not under a swept root -> not flagged


def test_sweep_clears_missing_when_folder_returns(tmp_path):
    from colophon.services.ingest import sweep_missing

    root = tmp_path / "lib"
    folder = root / "Dune"
    folder.mkdir(parents=True)
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=folder)
    b.missing = True  # was previously marked
    ctx.books.upsert(b)
    sweep_missing(ctx.books, [root])  # folder exists now
    assert ctx.books.get(b.id).missing is False  # self-heal


def test_missing_book_excluded_from_identify_candidates(tmp_path):
    root = tmp_path / "lib"
    folder = root / "Dune"
    folder.mkdir(parents=True)
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=folder)
    b.title = "Dune"
    b.missing = True
    ctx.books.upsert(b)
    assert b.id not in {c.id for c in AppController(ctx).identify_candidates()}


def test_remove_missing_deletes_record_and_history(tmp_path):
    root = tmp_path / "lib"
    folder = root / "Dune"
    folder.mkdir(parents=True)
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=folder)
    b.title = "Dune"
    b.missing = True
    ctx.books.upsert(b)
    AppController(ctx).remove_missing(b)
    assert ctx.books.get(b.id) is None


def _seed_book(root: Path, *parts: str, author: str) -> None:
    """Write a tagged silent mp3 at root/<parts...>/01.mp3 so scan() ingests a book
    with `author` and the maintained graph places it."""
    d = root.joinpath(*parts)
    d.mkdir(parents=True)
    f = d / "01.mp3"
    f.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[author]))
    tags.save(f)


def test_library_tree_franchise_from_override(tmp_path):
    root = tmp_path / "ingest"
    _seed_book(root, "Doctor Who", "Genesis", author="Terrance Dicks")
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.scan([root])
    b = ctx.books.list_all()[0]
    ctrl.set_node_classification(root / "Doctor Who", "franchise", "DOCTOR WHO")
    tree = ctrl.library_tree()
    assert [f.name for f in tree.franchises] == ["DOCTOR WHO"]
    assert b.id in {x.id for x in tree.franchises[0].books}


def test_library_tree_no_franchise_without_override(tmp_path):
    # Scan a real book (so it IS in the graph) but set no franchise override, so the
    # empty-franchises assertion actually exercises "no override -> no franchise tier".
    root = tmp_path / "ingest"
    _seed_book(root, "Author", "Book", author="Some Author")
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.scan([root])
    assert ctx.books.list_all()  # the book is scanned and in the graph
    assert ctrl.library_tree().franchises == []


def _two_author_books(tmp_path):
    root = tmp_path / "ingest"
    _seed_book(root, "BrandonSanderson", "Mistborn", author="Brandon Sanderson")
    _seed_book(root, "BSanderson", "Elantris", author="B. Sanderson")
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.scan([root])
    return ctrl


def test_set_entity_alias_merges_authors_in_library_tree(tmp_path):
    ctrl = _two_author_books(tmp_path)
    before = ctrl.library_tree()
    assert sorted(a.name for a in before.authors) == ["B. Sanderson", "Brandon Sanderson"]
    ctrl.set_entity_alias("author", "B. Sanderson", "Brandon Sanderson")
    after = ctrl.library_tree()
    assert [a.name for a in after.authors] == ["Brandon Sanderson"]


def test_clear_entity_alias_reverts(tmp_path):
    ctrl = _two_author_books(tmp_path)
    ctrl.set_entity_alias("author", "B. Sanderson", "Brandon Sanderson")
    assert [a.name for a in ctrl.library_tree().authors] == ["Brandon Sanderson"]
    ctrl.clear_entity_alias("author", "B. Sanderson")
    assert sorted(a.name for a in ctrl.library_tree().authors) == [
        "B. Sanderson",
        "Brandon Sanderson",
    ]


def _controller(tmp_path):
    from colophon.adapters.lazylibrarian import PathPatterns

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    # Folder pattern carries both $Author and $Series so canonical-projection
    # assertions on the organize path are meaningful (the default omits $Series).
    ctrl.ctx.patterns = PathPatterns(folder="$Author/$Series/$Title", single_file="$Title")
    return ctrl


def _persist_book(ctrl, *, title, authors=None, series=None):
    book = BookUnit.new(source_folder=ctrl.ctx.config.library_root / title)
    book.title = title
    book.authors = list(authors or [])
    if series is not None:
        book.series = list(series)
    ctrl.ctx.books.upsert(book)
    return book


def test_organize_targets_uses_canonical_author(tmp_path):
    ctrl = _controller(tmp_path)
    book = _persist_book(ctrl, title="A Book", authors=["B. Sanderson"])
    ctrl.set_entity_alias("author", "B. Sanderson", "Brandon Sanderson")
    [(_bid, target)] = ctrl.organize_targets([book])
    assert "Brandon Sanderson" in str(target)
    assert "B. Sanderson" not in str(target)


def test_tag_plan_projects_canonical_author(tmp_path):
    ctrl = _controller(tmp_path)
    book = _persist_book(ctrl, title="A Book", authors=["B. Sanderson"])
    ctrl.set_entity_alias("author", "B. Sanderson", "Brandon Sanderson")
    plan = ctrl.tag_plan(book)
    assert plan.target.artist == "Brandon Sanderson"


def test_canonical_series_flows_into_organize_and_tag(tmp_path):
    from colophon.core.models import SeriesRef

    ctrl = _controller(tmp_path)
    book = _persist_book(
        ctrl, title="A Book", authors=["x"],
        series=[SeriesRef(name="Mistborn Era 1", sequence=1.0)],
    )
    ctrl.set_entity_alias("series", "Mistborn Era 1", "Mistborn")
    assert ctrl.tag_plan(book).target.series == "Mistborn"
    [(_, target)] = ctrl.organize_targets([book])
    assert "Mistborn" in str(target) and "Era 1" not in str(target)


def test_process_book_organizes_and_tags_with_canonical_name(tmp_path):
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctrl = _controller(tmp_path)
    src_dir = tmp_path / "ingest" / "a-book"
    src_dir.mkdir(parents=True)
    src = src_dir / "a-book.m4b"
    src.write_bytes(b"\x00")
    book = _persist_book(ctrl, title="A Book", authors=["B. Sanderson"])
    book.source_files = [SourceFile(path=src, size=1, duration_seconds=60.0, ext=".m4b")]
    ctrl.ctx.books.upsert(book)
    ctrl.set_entity_alias("author", "B. Sanderson", "Brandon Sanderson")
    result = ctrl._process_book(book, EncodeJobOptions(
        encode=False, organize=True, delete_sources=False,
        patterns=ctrl.ctx.patterns,
    ))
    assert result.status == "done"
    assert "Brandon Sanderson" in str(book.output_path)
    assert "B. Sanderson" not in str(book.output_path)
    # the stored book stays raw
    assert ctrl.ctx.books.get(book.id).authors == ["B. Sanderson"]


def test_apply_scan_syncs_library_graph(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    assert ctx.library_graph.nodes == {}          # nothing loaded yet
    ctrl.scan([ingest])
    persisted_ids = {n.id for n in ctx.graph.load_all()[0]}
    assert persisted_ids
    assert set(ctx.library_graph.nodes) == persisted_ids
    ctx.close()


def test_scan_paths_missing_graph_reports_then_clears(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    assert ctrl.scan_paths_missing_graph() == [ingest]   # configured but never scanned
    ctrl.scan([ingest])
    assert ctrl.scan_paths_missing_graph() == []         # present after scan
    ctx.close()


def test_resync_roots_reflects_author_edit(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctx.books.list_all()[0]
    assert book.authors == ["Frank Herbert"]   # seed author, dropped by re-derivation below
    book.authors = ["Brand New Author"]
    ctx.books.upsert(book)
    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    edges = [e for e in ctx.library_graph.edges if e.kind == "author"]
    authors = {ctx.library_graph.nodes[e.dst].attrs["name"] for e in edges}
    assert authors == {"Brand New Author"}
    assert "Frank Herbert" not in authors      # re-derivation drops the old author
    persisted = {n.attrs.get("name") for n in ctx.graph.load_all()[0] if n.semantic == "author"}
    assert "Brand New Author" in persisted
    ctx.close()


def test_resync_skips_never_scanned_root(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ctrl._resync_roots({tmp_path / "never"})   # no skeleton -> no-op, no crash
    assert ctx.library_graph.nodes == {}
    ctx.close()


def test_save_fields_writes_through_to_graph(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctx.books.list_all()[0]
    ctrl.save_fields(book, {"author": "Edited Author"})
    authors = {
        ctx.library_graph.nodes[e.dst].attrs["name"]
        for e in ctx.library_graph.edges if e.kind == "author"
    }
    assert "Edited Author" in authors
    ctx.close()


def test_remove_missing_drops_book_and_orphan_entity_from_graph(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctx.books.list_all()[0]
    ctrl.remove_missing(book)
    book_nodes = [n for n in ctx.library_graph.nodes.values() if n.semantic == "book"]
    assert book_nodes == []                       # book node gone
    assert [e for e in ctx.library_graph.edges if e.kind == "author"] == []  # orphan entity edge gone
    ctx.close()


def test_set_franchise_override_writes_through_franchise_edge(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctx.books.list_all()[0]
    ctrl.set_node_classification(book.source_folder, "franchise", "My Franchise")
    fr = [e for e in ctx.library_graph.edges if e.kind == "franchise"]
    assert fr and ctx.library_graph.nodes[fr[0].dst].attrs["name"] == "My Franchise"
    ctx.close()


def test_library_tree_reads_authors_from_graph(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    tree = ctrl.library_tree()
    assert "Frank Herbert" in [a.name for a in tree.authors]
    book = ctx.books.list_all()[0]
    assert book.id in {b.id for b in tree.all_books}
    assert book.id not in {b.id for b in tree.needs_id}
    ctx.close()


def test_library_tree_conservative_book_absent_from_graph(tmp_path):
    from colophon.core.models import BookUnit
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "orphan")
    b.title, b.authors = "Orphan", ["Someone"]
    ctx.books.upsert(b)                 # in the store, never scanned -> not in the graph
    tree = ctrl.library_tree()
    assert b.id in {x.id for x in tree.all_books}    # visible in All
    assert b.id in {x.id for x in tree.needs_id}     # surfaces as needs_id (tripwire)
    assert b.id not in {x.id for a in tree.authors for s in a.series for x in s.books}
    assert b.id not in {x.id for a in tree.authors for x in a.standalone}
    ctx.close()


def test_rebuild_missing_graph_populates_from_books_without_scanning(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [tmp_path]
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "Author" / "Book")
    b.title, b.authors = "A Book", ["Some Author"]
    ctx.books.upsert(b)
    assert ctx.library_graph.nodes == {}
    before = ctx.books.list_all()

    healed = ctrl.rebuild_missing_graph()
    assert healed == 1
    authors = {
        ctx.library_graph.nodes[e.dst].attrs["name"]
        for e in ctx.library_graph.edges if e.kind == "author"
    }
    assert "Some Author" in authors
    assert ctx.books.list_all() == before        # books untouched
    assert ctrl.rebuild_missing_graph() == 0      # idempotent
    ctx.close()


def test_rebuild_missing_graph_noop_on_healthy_graph(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = _seed_ingest(tmp_path)
    ctx.config.scan_paths = [ingest]
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    assert ctrl.rebuild_missing_graph() == 0
    ctx.close()


def test_resync_seeds_book_only_root(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [tmp_path]
    ctrl = AppController(ctx)
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title, b.authors = "X", ["Y"]
    ctx.books.upsert(b)
    ctrl._resync_roots({ctrl._scan_root_for_path(b.source_folder)})
    assert any(n.semantic == "book" for n in ctx.library_graph.nodes.values())
    ctx.close()


def test_graph_neighborhood_resolves_book_label_and_confidence(tmp_path):
    from colophon.core.graph_records import EdgeRecord, NodeRecord
    from colophon.core.library_graph import LibraryGraph

    ctx = _ctx(tmp_path)
    controller = AppController(ctx)

    book = BookUnit.new(source_folder=tmp_path / "lib" / "Stella Rimington")
    book.title = "Close Call"
    book.confidence = 42.0
    ctx.books.upsert(book)

    author = NodeRecord(id="A", physical="directory", semantic="author",
                        root="/lib", attrs={"name": "Stella Rimington"})
    bnode = NodeRecord(id="B", physical=None, semantic="book",
                       root="/lib", attrs={"book_id": book.id})
    ctx.library_graph = LibraryGraph.from_records(
        [author, bnode], [EdgeRecord(src="A", kind="contains", dst="B", root="/lib")]
    )

    view = controller.graph_neighborhood("A")
    names = {n["name"] for n in view["echart"]["series"][0]["data"]}
    assert names == {"Stella Rimington", "Close Call"}
    assert set(view) == {"echart", "omitted"}

    # focal details are now a separate inspect call
    author_view = controller.graph_inspect("A")
    assert author_view.label == "Stella Rimington"
    assert author_view.kind == "author"

    book_view = controller.graph_inspect("B")
    assert book_view.confidence == 42.0
    assert book_view.label == "Close Call"

    hits = controller.graph_search("close")
    assert any(h["id"] == "B" and h["label"] == "Close Call" for h in hits)


def test_add_franchise_reclassifies_and_remove_reverts(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    # a made-up franchise name (not a built-in seed) so it classifies as author until declared
    for t, a in [("First Contact", "A Writer"), ("Second Contact", "B Writer")]:
        d = ingest / "Cosmic Legends" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode
    cl_id = DirectoryNode.id_for(ingest / "Cosmic Legends")

    assert ctrl.graph_for(ingest).directories[cl_id].kind == "author"

    ctrl.add_franchise("Cosmic Legends")
    assert "Cosmic Legends" in ctrl.list_franchises()
    assert ctrl.graph_for(ingest).directories[cl_id].kind == "franchise"   # cache invalidated

    ctrl.remove_franchise("Cosmic Legends")
    assert ctrl.graph_for(ingest).directories[cl_id].kind == "author"      # reverted
    ctx.close()


def test_builtin_franchise_classifies_without_declaration(tmp_path):
    # a folder named after a built-in franchise seed is a franchise tier with no user action
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    for t in ("Heir to the Empire", "Dark Force Rising"):
        d = ingest / "Star Wars" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode
    sw_id = DirectoryNode.id_for(ingest / "Star Wars")

    assert ctrl.list_franchises() == []                                    # nothing declared
    assert "Star Wars" in ctrl.builtin_franchises()
    assert ctrl.graph_for(ingest).directories[sw_id].kind == "franchise"   # seed recognized
    ctx.close()


def test_numbered_series_folder_classifies_series_and_cleans_titles(tmp_path):
    ctx = _ctx(tmp_path)
    ingest = tmp_path / "ingest"
    series = ingest / "Steven Brust" / "Vlad Taltos"
    for n, title in [("01", "Jhereg"), ("02", "Yendi"), ("03", "Teckla"), ("05", "Phoenix")]:
        d = series / f"{n} - {title}"
        d.mkdir(parents=True)
        (d / f"{n} - {title} - Steven Brust.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([ingest])

    from colophon.core.graph import DirectoryNode
    g = ctrl.graph_for(ingest)
    vlad_id = DirectoryNode.id_for(series)
    assert g.directories[vlad_id].kind == "series"                      # not author

    books = {b.title: b for b in ctx.books.list_all()}
    assert "Yendi" in books                                             # title cleaned
    assert books["Yendi"].series and books["Yendi"].series[0].name == "Vlad Taltos"
    assert books["Yendi"].series[0].sequence == 2.0
    assert books["Yendi"].authors != ["Vlad Taltos"]                    # the series name is no longer the author
    ctx.close()


def test_reprobe_recovers_zero_duration(tmp_path, make_audio):
    ctx = _ctx(tmp_path)
    make_audio("ingest/Dune/01.mp3", seconds=1)  # a real 1s audio file
    ingest = tmp_path / "ingest"
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    book = ctrl.ctx.books.list_all()[0]
    assert book.source_files[0].duration_seconds > 0.5  # scanned with a real duration

    # Simulate a pre-fallback scan that stored 0 duration for a real (nonzero-size) file.
    broken = book.model_copy(deep=True)
    broken.source_files[0].duration_seconds = 0.0
    ctrl.ctx.books.upsert(broken)
    assert ctrl.ctx.books.list_all()[0].source_files[0].duration_seconds == 0.0

    assert ctrl.reprobe_durations() == 1
    assert ctrl.ctx.books.list_all()[0].source_files[0].duration_seconds > 0.5


def test_reprobe_is_idempotent_when_nothing_missing(tmp_path, make_audio):
    ctx = _ctx(tmp_path)
    make_audio("ingest/Dune/01.mp3", seconds=1)
    ctrl = AppController(ctx)
    ctrl.scan([tmp_path / "ingest"])
    assert ctrl.reprobe_durations() == 0  # every file already has a duration


def test_reprobe_flags_empty_audio(tmp_path):
    from colophon.core.models import FindingCode
    ctx = _ctx(tmp_path)
    # a nonempty file with no audio (zero-filled placeholder), scanned as 0 duration
    folder = tmp_path / "ingest" / "Some Author" / "Some Book"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"\x00" * (128 * 1024))
    ctrl = AppController(ctx)
    ctrl.scan([tmp_path / "ingest"])
    book = ctrl.ctx.books.list_all()[0]
    assert book.source_files[0].duration_seconds == 0.0

    ctrl.reprobe_durations()
    flagged = ctrl.ctx.books.list_all()[0]
    assert any(f.code is FindingCode.EMPTY_AUDIO for f in flagged.findings)


def test_books_for_scope_and_pipeline_counts(tmp_path):
    from colophon.core.models import BookState, BookUnit
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    def mk(name, state):
        b = BookUnit.new(source_folder=tmp_path / name)
        b.state = state
        ctx.books.upsert(b)
        return b

    ready = mk("a", BookState.READY)
    ident = mk("b", BookState.IDENTIFIED)
    review = mk("c", BookState.NEEDS_REVIEW)

    counts = ctrl.pipeline_counts()
    assert counts["ready"] == 1
    assert counts["identified"] == 1

    assert {x.id for x in ctrl.books_for_scope("ready")} == {ready.id}
    assert {x.id for x in ctrl.books_for_scope("all")} == {ready.id, ident.id, review.id}
    assert {x.id for x in ctrl.books_for_scope("selected", {ident.id})} == {ident.id}
    assert ctrl.books_for_scope("selected", set()) == []
    # A selected id that no longer exists is silently skipped, not hydrated as None.
    assert {x.id for x in ctrl.books_for_scope("selected", {ident.id, "gone"})} == {ident.id}

    # scope_counts feeds the selector's Ready/All labels from the stored state column, so they
    # agree with pipeline_counts and with the set books_for_scope("ready") actually resolves.
    scope = ctrl.scope_counts()
    assert scope["ready"] == counts["ready"]
    assert scope["total"] == len(ctrl.books_for_scope("all"))


def test_dedupe_colliding_covers_clears_only_shared_paths(tmp_path):
    # Clustered books that shared a folder cached to one folder-keyed file (pre-fix), so
    # a cover_path held by >1 book is a collision. The repair clears exactly those, leaving
    # a solo book's unique cover untouched, and is idempotent.
    ctx = _ctx(tmp_path)
    shared = tmp_path / "C S Lewis"
    collide = shared / "cover.jpg"
    a = BookUnit.new(source_folder=shared)
    a.id, a.cover_path, a.cover_url = "aaaa000000000001", collide, "https://x/a.jpg"
    b = BookUnit.new(source_folder=shared)
    b.id, b.cover_path, b.cover_url = "bbbb000000000002", collide, "https://x/b.jpg"
    solo = BookUnit.new(source_folder=tmp_path / "Solo")
    solo.cover_path = tmp_path / "Solo" / "cover.jpg"
    for bk in (a, b, solo):
        ctx.books.upsert(bk)

    assert AppController(ctx).dedupe_colliding_covers() == 2
    assert ctx.books.get(a.id).cover_path is None
    assert ctx.books.get(b.id).cover_path is None
    assert ctx.books.get(solo.id).cover_path is not None  # unique, untouched
    assert AppController(ctx).dedupe_colliding_covers() == 0  # idempotent once healed
    ctx.close()


def test_process_book_no_encode_reorgs_multipart(tmp_path):
    from colophon.adapters.lazylibrarian import PathPatterns
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    src = tmp_path / "ingest" / "book"
    src.mkdir(parents=True)
    (src / "a.mp3").write_bytes(b"one")
    (src / "b.mp3").write_bytes(b"two")

    book = BookUnit.new(source_folder=src)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.source_files = [
        SourceFile(path=src / "a.mp3", size=3, duration_seconds=1.0, ext=".mp3"),
        SourceFile(path=src / "b.mp3", size=3, duration_seconds=1.0, ext=".mp3"),
    ]
    ctx.books.upsert(book)
    ctx.config.library_root = tmp_path / "library"

    options = EncodeJobOptions(
        encode=False,
        organize=True,
        patterns=PathPatterns(
            folder="$Author/$Title",
            single_file="$Title[ - Part $Part of $Total]",
        ),
    )
    result = ctrl._process_book(book, options)
    assert result.status == "done", result.detail

    folder = tmp_path / "library" / "Frank Herbert" / "Dune"
    part1 = folder / "Dune - Part 01 of 02.mp3"
    part2 = folder / "Dune - Part 02 of 02.mp3"
    assert part1.exists(), f"expected {part1}"
    assert part2.exists(), f"expected {part2}"
    # tag_file writes ID3 headers onto the copy; verify via embedded track numbers
    from colophon.adapters.tags import read_embedded_tags as _ret
    assert _ret(part1).track == 1
    assert _ret(part2).track == 2
    ctx.close()


def test_process_book_no_encode_blocks_on_ambiguous_order(tmp_path):
    from colophon.adapters.lazylibrarian import PathPatterns
    from colophon.controller import EncodeJobOptions
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)

    src = tmp_path / "ingest" / "book"
    dup = src / "dup"
    dup.mkdir(parents=True)
    (src / "track.mp3").write_bytes(b"one")
    (dup / "track.mp3").write_bytes(b"two")

    book = BookUnit.new(source_folder=src)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    book.source_files = [
        SourceFile(path=src / "track.mp3", size=3, duration_seconds=1.0, ext=".mp3"),
        SourceFile(path=dup / "track.mp3", size=3, duration_seconds=1.0, ext=".mp3"),
    ]
    ctx.books.upsert(book)
    ctx.config.library_root = tmp_path / "library"

    options = EncodeJobOptions(
        encode=False,
        organize=True,
        patterns=PathPatterns(folder="$Author", single_file="$Title"),
    )
    result = ctrl._process_book(book, options)
    assert result.status == "failed"
    assert result.detail is not None and "couldn't order" in result.detail
    ctx.close()


def test_library_tree_warm_predicate(tmp_path):
    from colophon.core.models import BookUnit
    ctrl = _controller(tmp_path)
    # cold before any derivation
    assert ctrl.library_tree_warm() is False
    ctrl.library_tree()                       # derive + memoize
    assert ctrl.library_tree_warm() is True    # warm for the current generation
    # a mutation that bumps a generation makes it cold again
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "T"
    ctrl.ctx.books.upsert(b)
    assert ctrl.library_tree_warm() is False


def test_books_for_scope_ready_state_param(tmp_path):
    from colophon.core.models import BookState, BookUnit
    ctrl = _controller(tmp_path)

    def _mk(name: str, state: BookState) -> BookUnit:
        b = BookUnit.new(source_folder=tmp_path / name)
        b.title = name
        b.state = state
        ctrl.ctx.books.upsert(b)
        return b

    _mk("ident", BookState.IDENTIFIED)
    _mk("ready", BookState.READY)

    # default ready_state is READY (Persist behavior, unchanged)
    assert {b.title for b in ctrl.books_for_scope("ready")} == {"ready"}
    # Match passes IDENTIFIED
    assert {b.title for b in ctrl.books_for_scope("ready", ready_state=BookState.IDENTIFIED)} == {"ident"}
    # scope_counts follows the same tier
    assert ctrl.scope_counts()["ready"] == 1
    assert ctrl.scope_counts(ready_state=BookState.IDENTIFIED)["ready"] == 1


def test_remap_embedded_moves_a_file_tag_into_a_field(tmp_path, make_audio):
    from mutagen.id3 import ID3, TPE1

    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Book/01.mp3", seconds=1)
    tags = ID3()
    tags.add(TPE1(encoding=3, text=["Vonda N. McIntyre"]))   # artist tag
    tags.save(a)
    book = BookUnit.new(source_folder=a.parent)
    book.title = "Placeholder"
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    batch = AppController(ctx).remap_embedded(book, tag="artist", dst="author")
    assert batch is not None
    persisted = ctx.books.get(book.id)
    assert persisted.authors == ["Vonda N. McIntyre"]
    ctx.close()


def test_remap_embedded_none_when_tag_absent(tmp_path, make_audio):
    from colophon.core.models import SourceFile

    ctx = _ctx(tmp_path)
    a = make_audio("Book/01.mp3", seconds=1)   # no artist tag
    book = BookUnit.new(source_folder=a.parent)
    book.source_files = [SourceFile(path=a, size=a.stat().st_size, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    assert AppController(ctx).remap_embedded(book, tag="artist", dst="author") is None
    assert ctx.books.get(book.id).authors == []
    ctx.close()


def test_match_field_values_keeps_asin_only_from_audiobook_sources():
    # A physical/Kindle ASIN from a book source (Hardcover) must not become the book's asin — it's
    # the wrong product for an audiobook and would dead-end the Audible lookup. An Audible source's
    # asin is kept.
    from colophon.core.sources import SourceResult

    audible = SourceResult(provider="audnexus", title="X", asin="B0AUDIBLE0")
    physical = SourceResult(provider="hardcover", title="X", asin="0306406152")
    assert AppController.match_field_values(audible).get("asin") == "B0AUDIBLE0"
    assert "asin" not in AppController.match_field_values(physical)
    # the rest of the physical match still comes through
    assert AppController.match_field_values(physical).get("title") == "X"
