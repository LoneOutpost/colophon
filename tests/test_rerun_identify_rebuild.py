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


def _single_book(tmp_path):
    """One untagged single-file book in a folder whose NAME carries the title (the #328 case)."""
    ingest = tmp_path / "ingest"
    d = ingest / "1981 - Cujo (read by Lorna Raver)"
    d.mkdir(parents=True)
    (d / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    c = AppController(ctx)
    c.scan()
    return ctx, c, ingest


def test_rerun_identify_rederives_a_stale_weak_name(tmp_path):
    # The crux the wedge exists for: re-running IDENTIFY is a hard clear-and-rebuild, so a
    # stale-but-present auto-derived (weak) name is re-derived from the folder. The old shallow
    # path left a present weak title alone (reconcile's fill-empty gate never overwrote it), so a
    # user could not get a better name by re-running. This is what makes the button do real work.
    ctx, c, _ingest = _single_book(tmp_path)
    book = next(iter(ctx.books.list_all()))
    assert book.title == "Cujo"                       # scan already derived it from the folder
    book.title = "Wrong Guess"
    book.provenance["title"] = "directory"            # weak / auto-derived, but present
    ctx.books.upsert(book)

    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).title == "Cujo"     # re-derived, not left at the stale value
    ctx.close()


def test_rerun_identify_preserves_a_manual_name(tmp_path):
    # The clear is auto-derived only: a manual title is authoritative and survives the rebuild.
    ctx, c, _ingest = _single_book(tmp_path)
    book = next(iter(ctx.books.list_all()))
    book.title = "Manual Name"
    book.provenance["title"] = "manual"
    ctx.books.upsert(book)

    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).title == "Manual Name"
    ctx.close()


def test_reclassify_reflected_after_rerun_identify(tmp_path):
    # A manual reclassify is reflected in identity after the re-run. (Note: an author reclassify
    # also fills down via _resync_roots at reclassify time; this pins that the re-run, which runs
    # the whole resolving walk, does not undo it.)
    ctx, c, ingest = _library(tmp_path)
    book = next(iter(ctx.books.list_all()))

    c.set_node_classification(ingest / "SomeFolder", "author", "Custom Author")
    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).authors == ["Custom Author"]
    ctx.close()


def test_rerun_identify_preserves_a_manual_author(tmp_path):
    # Auto-derived fields refresh on re-run; a manual value is authoritative and survives.
    ctx, c, _ingest = _library(tmp_path)
    book = next(iter(ctx.books.list_all()))
    book.authors = ["Hand Typed"]
    book.provenance["authors"] = "manual"
    ctx.books.upsert(book)

    c.rerun_phase([book], Phase.IDENTIFY)

    assert ctx.books.get(book.id).authors == ["Hand Typed"]
    ctx.close()


def test_rerun_identify_on_one_book_preserves_a_siblings_manual_edit(tmp_path):
    # The re-run re-resolves the whole folder; a sibling's manual edit must not be clobbered.
    ctx, c, _ingest = _library(tmp_path)
    books = {b.source_folder.name: b for b in ctx.books.list_all()}
    elantris, warbreaker = books["Elantris"], books["Warbreaker"]
    warbreaker.authors = ["Sibling Manual"]
    warbreaker.provenance["authors"] = "manual"
    ctx.books.upsert(warbreaker)

    c.rerun_phase([elantris], Phase.IDENTIFY)

    assert ctx.books.get(warbreaker.id).authors == ["Sibling Manual"]
    ctx.close()
