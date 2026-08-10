"""reidentify_from_cache: library-wide re-run of CATEGORIZE+IDENTIFY from cached tags (no disk),
then the same graph re-derive a recompute does. Clears weak (folder/filename) identity so it
re-derives from cache; hard (tag/manual/match) identity survives."""
from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, EmbeddedTags, SourceFile


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
