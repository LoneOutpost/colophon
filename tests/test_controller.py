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
                            progress=None, byte_progress=None, cancel=None):
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
                            progress=None, byte_progress=None, cancel=None):
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
                            progress=None, byte_progress=None, cancel=None):
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
                            progress=None, byte_progress=None, cancel=None):
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
    assert ctrl.source_label("manual") == "Manual"
    assert ctrl.source_label("googlebooks") == "Google Books"
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
    assert p.cover_path == book.source_folder / "cover.png"
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
    assert ctx.books.get(book.id).cover_path == book.source_folder / "cover.jpg"
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


class _CapturingSource:
    name = "cap"

    def __init__(self):
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return [SourceResult(provider="cap", title="Dune", authors=["Frank Herbert"])]


def test_quick_match_scan_passes_search_fields(tmp_path):
    src = _CapturingSource()
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


def test_import_ll_patterns_reads_folder_and_single_file(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[POSTPROCESS]\n"
        "audiobook_dest_folder = $Author/$Series/$Title\n"
        "audiobook_single_file = $Title ($PubYear)\n"
    )
    ctx = _ctx(tmp_path)
    folder, file = AppController(ctx).import_ll_patterns(ini)
    assert folder == "$Author/$Series/$Title"
    assert file == "$Title ($PubYear)"
    ctx.close()


def test_import_ll_patterns_defaults_file_to_title(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[POSTPROCESS]\naudiobook_dest_folder = $Author/$Title\n")
    ctx = _ctx(tmp_path)
    folder, file = AppController(ctx).import_ll_patterns(ini)
    assert folder == "$Author/$Title"
    assert file == "$Title"  # no audiobook_single_file -> sensible single-file default
    ctx.close()


def test_import_ll_patterns_missing_file_raises(tmp_path):
    import pytest

    ctx = _ctx(tmp_path)
    with pytest.raises(FileNotFoundError):
        AppController(ctx).import_ll_patterns(tmp_path / "absent.ini")
    ctx.close()


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
