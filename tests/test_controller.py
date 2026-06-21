import asyncio
import json as _json
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookState, BookUnit
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


async def test_identify_pending_sets_state(tmp_path):
    src = _StubSource([SourceResult(provider="stub", title="Dune", authors=["Frank Herbert"])])
    ctx = _ctx(tmp_path, sources=[src])
    ingest = _seed_ingest(tmp_path)
    ctrl = AppController(ctx)
    ctrl.scan([ingest])
    await ctrl.identify_pending()
    states = {b.state for b in ctx.books.list_all()}
    assert states <= {BookState.READY, BookState.NEEDS_REVIEW}
    ctx.close()


def test_dashboard_stats_counts_by_state(tmp_path):
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "a")
    a.state = BookState.READY
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.state = BookState.NEEDS_REVIEW
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
    assert results[0].detail == "collision"
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


def test_edit_field_writes_sidecar(tmp_path):
    ctx = _ctx(tmp_path)
    b = _book_in(ctx, tmp_path / "ingest" / "x")
    ctrl = AppController(ctx)
    ctrl.edit_field(b, "title", "Right")
    raw = _json.loads((b.source_folder / "metadata.json").read_text())
    assert raw["title"] == "Right"
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


def test_bulk_edit_writes_each_sidecar(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book_in(ctx, tmp_path / "ingest" / "a")
    b = _book_in(ctx, tmp_path / "ingest" / "b")
    AppController(ctx).bulk_edit([a, b], "publisher", "Tor")
    for book in (a, b):
        raw = _json.loads((book.source_folder / "metadata.json").read_text())
        assert raw["publisher"] == "Tor"
    ctx.close()


def test_undo_rewrites_sidecar_to_restored_value(tmp_path):
    ctx = _ctx(tmp_path)
    b = _book_in(ctx, tmp_path / "ingest" / "x")
    ctrl = AppController(ctx)
    batch = ctrl.edit_field(b, "title", "Right")
    ctrl.undo(batch)
    raw = _json.loads((b.source_folder / "metadata.json").read_text())
    assert raw["title"] == "Wrong"  # sidecar reflects the undo
    ctx.close()


def test_sidecar_write_failure_does_not_break_edit(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    b = _book_in(ctx, tmp_path / "ingest" / "x")
    ctrl = AppController(ctx)
    monkeypatch.setattr("colophon.controller.write_sidecar", lambda *a, **k: (_ for _ in ()).throw(OSError("nfs down")))
    # edit must still persist to the DB despite the sidecar write failing
    ctrl.edit_field(b, "title", "Right")
    assert ctx.books.get(b.id).title == "Right"
    ctx.close()


def test_sidecar_typeerror_does_not_break_edit(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    b = _book_in(ctx, tmp_path / "ingest" / "x")
    ctrl = AppController(ctx)
    monkeypatch.setattr(
        "colophon.controller.write_sidecar",
        lambda *a, **k: (_ for _ in ()).throw(TypeError("not serializable")),
    )
    # a non-OSError from the sidecar write must still not lose the DB edit
    ctrl.edit_field(b, "title", "Right")
    assert ctx.books.get(b.id).title == "Right"
    ctx.close()


def test_edit_preserves_scanned_sidecar_field(tmp_path):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "ingest" / "withdesc"
    folder.mkdir(parents=True, exist_ok=True)
    b = BookUnit.new(source_folder=folder)
    b.title = "Old Title"
    b.authors = ["Frank Herbert"]
    b.description = "A desert planet epic."
    ctx.books.upsert(b)
    AppController(ctx).edit_field(b, "title", "New Title")
    raw = _json.loads((folder / "metadata.json").read_text())
    assert raw["title"] == "New Title"
    assert raw["description"] == "A desert planet epic."
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


def test_preview_filename_parse_returns_fields(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    parsed = AppController(ctx).preview_filename_parse(book, "%author% - %title%")
    assert parsed == {"author": "Brandon Sanderson", "title": "Mistborn"}
    ctx.close()


def test_preview_filename_parse_returns_empty_on_no_match(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "nomatch.mp3")
    parsed = AppController(ctx).preview_filename_parse(book, "%author% - %title%")
    assert parsed == {}
    ctx.close()


def test_preview_filename_parse_raises_on_bad_template(tmp_path):
    import pytest

    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "x.mp3")
    with pytest.raises(ValueError):
        AppController(ctx).preview_filename_parse(book, "%bogus%")
    ctx.close()


def test_apply_filename_parse_writes_selected_fields(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    n = AppController(ctx).apply_filename_parse([book], "%author% - %title%", {"author", "title"})
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
    AppController(ctx).apply_filename_parse([book], "%author% - %title%", {"title"})
    got = ctx.books.get(book.id)
    assert got.title == "Mistborn"
    assert got.authors == []  # author parsed but not selected, so not written
    ctx.close()


def test_apply_filename_parse_sets_series_before_sequence(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    AppController(ctx).apply_filename_parse([book], "%series% #%sequence%", {"series", "sequence"})
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
    n = AppController(ctx).apply_filename_parse([ok, bad], "%author% - %title%", {"author", "title"})
    assert n == 1  # only the matching book counted
    assert ctx.books.get(bad.id).title is None
    ctx.close()


def test_apply_filename_parse_drops_sequence_without_series(tmp_path):
    # A pattern that yields sequence but not series, on a book with no series,
    # is a no-op for sequence: it must not be counted or recorded.
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    ctrl = AppController(ctx)
    n = ctrl.apply_filename_parse([book], "%title% #%sequence%", {"title", "sequence"})
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
    n = ctrl.apply_filename_parse([book], "%title% #%sequence%", {"sequence"})
    assert n == 0
    ctx.close()


def test_filename_parse_updates_matches_apply(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Mistborn #1.mp3")
    updates = AppController(ctx).filename_parse_updates(
        book, "%title% #%sequence%", {"title", "sequence"}
    )
    assert updates == {"title": "Mistborn"}  # sequence dropped, no series
    ctx.close()


def test_apply_filename_parse_is_undoable(tmp_path):
    ctx = _ctx(tmp_path)
    book = _book_named(ctx, tmp_path, "Brandon Sanderson - Mistborn.mp3")
    ctrl = AppController(ctx)
    ctrl.apply_filename_parse([book], "%author% - %title%", {"title"})
    assert ctrl.undo_last() is True
    assert ctx.books.get(book.id).title is None
    ctx.close()


def test_save_and_remove_filename_pattern_persist(tmp_path):
    from colophon.adapters.config import load_config

    cfg_path = tmp_path / "c.toml"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"), config_path=cfg_path)
    ctrl = AppController(ctx)
    ctrl.save_filename_pattern("%author% - %title%")
    ctrl.save_filename_pattern("%author% - %title%")  # dedup: no second copy
    ctrl.save_filename_pattern("%series% #%sequence% - %title%")
    assert load_config(cfg_path).saved_filename_patterns == [
        "%author% - %title%",
        "%series% #%sequence% - %title%",
    ]
    ctrl.remove_filename_pattern("%author% - %title%")
    assert load_config(cfg_path).saved_filename_patterns == ["%series% #%sequence% - %title%"]
    ctx.close()


def test_save_filename_pattern_rejects_bad_template(tmp_path):
    import pytest

    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    with pytest.raises(ValueError):
        ctrl.save_filename_pattern("%nope%")
    assert ctx.config.saved_filename_patterns == []
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
    # sidecar written to source folder
    import json
    raw = json.loads((book.source_folder / "metadata.json").read_text())
    assert raw["title"] == "Dune"
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
        book, title="Edited", author="A", series="S", asin="B01", source_name="audnexus"
    )
    q = captured["q"]
    assert (q.title, q.author, q.series, q.asin) == ("Edited", "A", "S", "B01")
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


def test_foster_files_creates_subdir_books_and_updates_parent(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Mistborn.mp3").write_bytes(b"")
    (author / "Legion.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])  # one grouped book holding both loose files
    grouped_id = BookUnit.new(source_folder=author).id
    assert len(ctx.books.get(grouped_id).source_files) == 2

    results = ctrl.foster_files([author / "Mistborn.mp3"])
    assert len(results) == 1 and results[0].ok
    assert results[0].destination == author / "Mistborn" / "Mistborn.mp3"

    # The fostered file now scans as its own book...
    fostered_id = BookUnit.new(source_folder=author / "Mistborn").id
    assert ctx.books.get(fostered_id) is not None
    # ...and the parent book retains only the remaining loose file.
    parent_files = [sf.path.name for sf in ctx.books.get(grouped_id).source_files]
    assert parent_files == ["Legion.mp3"]
    ctx.close()


def test_foster_files_prunes_parent_when_emptied(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Solo Author"
    author.mkdir(parents=True)
    (author / "OnlyBook.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    grouped_id = BookUnit.new(source_folder=author).id
    assert ctx.books.get(grouped_id) is not None

    ctrl.foster_files([author / "OnlyBook.mp3"])
    # Parent folder now has no direct audio -> its stale book is removed.
    assert ctx.books.get(grouped_id) is None
    assert ctx.books.get(BookUnit.new(source_folder=author / "OnlyBook").id) is not None
    ctx.close()


def test_foster_files_reports_per_file_failure(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Author"
    author.mkdir(parents=True)
    (author / "Book.mp3").write_bytes(b"")
    (author / "Book").mkdir()  # collision: foster target already exists
    ctrl = AppController(ctx)
    results = ctrl.foster_files([author / "Book.mp3"])
    assert len(results) == 1 and results[0].ok is False
    assert results[0].error and results[0].destination is None
    assert (author / "Book.mp3").exists()  # left in place
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

    async def fake_download(client, torrent, dest_root, *, progress=None):
        folder = dest_root / "Mistborn"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "01.mp3").write_bytes(b"")
        return AcquireResult(folder=folder, files=[AcquiredFile("01.mp3", folder / "01.mp3", True)])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download)

    result, book_ids = await ctrl.rd_download("a")
    assert result.any_ok is True
    assert len(book_ids) == 1
    assert ctx.books.get(book_ids[0]) is not None
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


async def test_restructure_as_books_sets_author_title(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Shiloh Walker"
    author.mkdir(parents=True)
    (author / "Burning Up.mp3").write_bytes(b"")
    (author / "the-darkest-part.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    ctrl.scan([author])
    result = await ctrl.restructure_as_books(
        [author / "Burning Up.mp3", author / "the-darkest-part.mp3"]
    )
    assert result.fostered == 2
    b1 = ctx.books.get(BookUnit.new(source_folder=author / "Burning Up").id)
    b2 = ctx.books.get(BookUnit.new(source_folder=author / "the-darkest-part").id)
    assert b1.authors == ["Shiloh Walker"] and b1.title == "Burning Up"
    assert b2.authors == ["Shiloh Walker"] and b2.title == "The Darkest Part"
    assert set(result.book_ids) == {b1.id, b2.id}
    ctx.close()


async def test_restructure_as_books_author_override(tmp_path):
    ctx = _ctx(tmp_path)
    folder = tmp_path / "ingest" / "TE_Audiobooks_S"
    folder.mkdir(parents=True)
    (folder / "Book One.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    result = await ctrl.restructure_as_books(
        [folder / "Book One.mp3"], author_override="Shiloh Walker"
    )
    b = ctx.books.get(BookUnit.new(source_folder=folder / "Book One").id)
    assert b.authors == ["Shiloh Walker"]
    assert b.title == "Book One"
    assert result.fostered == 1
    ctx.close()


async def test_restructure_as_books_writes_tags(tmp_path):
    from colophon.adapters.tags import read_embedded_tags
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Shiloh Walker"
    author.mkdir(parents=True)
    (author / "Burning Up.mp3").write_bytes(b"")
    ctrl = AppController(ctx)
    result = await ctrl.restructure_as_books([author / "Burning Up.mp3"], write_tags=True)
    dest = author / "Burning Up" / "Burning Up.mp3"
    tags = read_embedded_tags(dest)
    assert tags.title == "Burning Up"
    assert tags.artist == "Shiloh Walker"
    assert result.retagged == 1
    ctx.close()


async def test_restructure_as_books_reports_failure_without_aborting(tmp_path):
    ctx = _ctx(tmp_path)
    author = tmp_path / "ingest" / "Author"
    author.mkdir(parents=True)
    (author / "Good.mp3").write_bytes(b"")
    (author / "Bad.mp3").write_bytes(b"")
    (author / "Bad").mkdir()  # collision: foster target for Bad.mp3 already exists
    ctrl = AppController(ctx)
    result = await ctrl.restructure_as_books([author / "Good.mp3", author / "Bad.mp3"])
    assert result.fostered == 1
    assert len(result.failures) == 1
    assert result.failures[0].source.name == "Bad.mp3"
    good = ctx.books.get(BookUnit.new(source_folder=author / "Good").id)
    assert good is not None and good.title == "Good"
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
    assert ctrl.source_label("manual") == "Manual"
    assert ctrl.source_label("googlebooks") == "Google Books"
    ctx.close()


def test_genre_policy_reflects_config(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.genre_mapping = {"scifi": "Science Fiction"}
    ctx.config.accepted_genres = ["Science Fiction"]
    ctx.config.genre_whitelist_enabled = True
    pol = AppController(ctx).genre_policy()
    assert pol.canonicalize(["scifi", "Dragons"]) == ["Science Fiction"]
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
