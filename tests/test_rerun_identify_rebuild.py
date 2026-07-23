from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import Phase


def _library(tmp_path):
    """Two untagged single-file books under one parent folder, fully scanned."""
    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "SomeFolder" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    c = AppController(ctx)
    c.scan()
    return ctx, c, ingest


def test_reclassify_then_rerun_identify_propagates_author(tmp_path):
    # The crux: a manual reclassify only takes effect through the resolving walk. The old shallow
    # per-book re-run ignored node_overrides entirely, so a reclassify was "worthless for
    # identification". Routed through the rebuild, re-running IDENTIFY applies the override.
    ctx, c, ingest = _library(tmp_path)
    book = next(iter(ctx.books.list_all()))

    c.set_node_classification(ingest / "SomeFolder", "author", "Custom Author")
    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).authors == ["Custom Author"]
    ctx.close()


def test_rerun_identify_preserves_a_manual_author(tmp_path):
    # Auto-derived fields refresh on re-run; a manual value is authoritative and survives.
    ctx, c, ingest = _library(tmp_path)
    book = next(iter(ctx.books.list_all()))
    book.authors = ["Hand Typed"]
    book.provenance["authors"] = "manual"
    ctx.books.upsert(book)

    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).authors == ["Hand Typed"]
    ctx.close()


def test_rerun_identify_on_one_book_preserves_a_siblings_manual_edit(tmp_path):
    # The re-run re-resolves the whole folder; a sibling's manual edit must not be clobbered.
    ctx, c, ingest = _library(tmp_path)
    books = {b.source_folder.name: b for b in ctx.books.list_all()}
    elantris, warbreaker = books["Elantris"], books["Warbreaker"]
    warbreaker.authors = ["Sibling Manual"]
    warbreaker.provenance["authors"] = "manual"
    ctx.books.upsert(warbreaker)

    c.rerun_phase([elantris], Phase.IDENTIFY)

    assert ctx.books.get(warbreaker.id).authors == ["Sibling Manual"]
    ctx.close()
