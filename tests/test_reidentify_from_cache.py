"""reidentify_from_cache: library-wide re-run of CATEGORIZE+IDENTIFY from cached tags (no disk),
then the same graph re-derive a recompute does. Clears weak (folder/filename) identity so it
re-derives from cache; hard (tag/manual/match) identity survives."""
import shutil

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_records
from colophon.core.models import BookState, BookUnit, EmbeddedTags, Phase, PhaseState, SourceFile
from colophon.core.node_classify import classify_nodes
from colophon.core.phases import mark, resync_state
from colophon.services.graph_build import build_graph


def _ctx(tmp_path):
    return AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"]))


def _cached_book(tmp_path, folder, fname, tags):
    """A stored book whose single source file carries cached tags but whose path does not exist
    on disk — so any code that reads the file (rather than the cache) would raise."""
    d = tmp_path / "ingest" / folder
    book = BookUnit.new(source_folder=d)
    book.source_files = [SourceFile(path=d / fname, size=1, duration_seconds=60.0, ext="mp3",
                                    tags=tags)]
    return book


def test_reidentify_refreshes_weak_field_from_cache_without_disk(tmp_path):
    ctx = _ctx(tmp_path)
    # A stale, folder-derived (WEAK) title; the cached tag names the real one. No file on disk.
    book = _cached_book(tmp_path, "Brandon Sanderson/Elantris", "01.mp3",
                        EmbeddedTags(title="Elantris", artist="Brandon Sanderson"))
    book.title = "STALE"
    book.provenance["title"] = "directory"   # weak -> re-identify clears + re-derives it
    ctx.books.upsert(book)

    summary = AppController(ctx).reidentify_from_cache()

    stored = ctx.books.get(book.id)
    assert stored.title == "Elantris"          # cleared weak title, re-derived from the cached tag
    assert stored.authors == ["Brandon Sanderson"]
    assert summary.updated >= 1
    ctx.close()


def test_reidentify_preserves_hard_identity(tmp_path):
    ctx = _ctx(tmp_path)
    # A title with MANUAL (hard) provenance must survive even though the cache names another.
    book = _cached_book(tmp_path, "Author/Book", "01.mp3",
                        EmbeddedTags(title="Cache Title", artist="A"))
    book.title = "Manual Keep"
    book.provenance["title"] = "manual"
    ctx.books.upsert(book)

    AppController(ctx).reidentify_from_cache()

    assert ctx.books.get(book.id).title == "Manual Keep"   # hard identity preserved
    ctx.close()


def test_reidentify_leaves_uncached_missing_file_book_unchanged(tmp_path):
    ctx = _ctx(tmp_path)
    # tags=None + non-existent path: the front half's read fails, is logged, and the book's
    # fields are left as-is (degrades to graph-only for this book) rather than raising.
    book = _cached_book(tmp_path, "Author/Book2", "gone.mp3", None)
    book.title = "Kept"
    ctx.books.upsert(book)

    summary = AppController(ctx).reidentify_from_cache()

    assert ctx.books.get(book.id).title == "Kept"
    assert isinstance(summary.updated, int)
    ctx.close()


def _seed(tmp_path):
    """Two titles under one author folder, persisted as a completed local scan would leave them.
    build_graph runs SEARCH, so each SourceFile carries cached tags — the re-identify front half
    then runs from that cache with no disk read (the rmtree test relies on this)."""
    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "Brandon Sanderson" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    g = build_graph(ctx.books, ingest, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    classify_graph(g, root=ingest)
    classify_nodes(g, books, root=ingest, overrides={})
    for b in books:
        mark(b, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(b)
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(ingest), *graph_records(g, books, root=ingest))
    return ctx, ingest


def test_reidentify_is_disk_free_and_persists_verdict(tmp_path):
    ctx, ingest = _seed(tmp_path)
    # Corrupt the stored confidence + blank the verdict so a correct re-derive must change them.
    for b in ctx.books.list_all():
        b.identity_confidence = 0.0
        b.title_corroboration = None
        ctx.books.upsert(b)
    ctrl = AppController(ctx)
    shutil.rmtree(ingest)  # no disk access: source tree gone, the cache serves the front half

    summary = ctrl.reidentify_from_cache()

    assert summary.updated >= 2
    for b in ctx.books.list_all():
        assert b.identity_confidence > 0
        assert b.title_corroboration in {"agree", "abstain", "contradict"}
    ctx.close()


def test_reidentify_reports_out_of_review_movement(tmp_path):
    ctx, _ingest = _seed(tmp_path)
    # Force one stored book to look stuck in review; the graph gives a strong author, so a
    # re-identify re-derives it back to identified -> it leaves review.
    victim = ctx.books.list_all()[0]
    victim.state = BookState.NEEDS_REVIEW
    victim.identity_confidence = 5.0
    ctx.books.upsert(victim)

    summary = AppController(ctx).reidentify_from_cache()

    assert summary.out_of_review == 1
    assert summary.updated >= 1
    ctx.close()


def test_reidentify_is_idempotent(tmp_path):
    ctx, _ = _seed(tmp_path)
    ctrl = AppController(ctx)
    ctrl.reidentify_from_cache()                 # first run settles any drift
    second = ctrl.reidentify_from_cache()
    assert second.updated == 0
    assert second.into_review == 0 and second.out_of_review == 0
    ctx.close()


def test_reidentify_empty_scan_paths_is_a_noop(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", scan_paths=[]))
    summary = AppController(ctx).reidentify_from_cache()
    assert (summary.updated, summary.into_review, summary.out_of_review) == (0, 0, 0)
    ctx.close()


def test_reidentify_cleans_dirty_title_and_year(tmp_path):
    ctx, _ = _seed(tmp_path)
    victim = ctx.books.list_all()[0]
    victim.title = "SB 01 - " + victim.title   # e.g. "SB 01 - Elantris"
    victim.publish_year = 1
    ctx.books.upsert(victim)
    ctrl = AppController(ctx)

    ctrl.reidentify_from_cache()

    healed = ctx.books.get(victim.id)
    assert healed.title == "Elantris"
    assert healed.publish_year is None
    assert ctrl.reidentify_from_cache().updated == 0   # idempotent: second pass changes nothing
    ctx.close()
