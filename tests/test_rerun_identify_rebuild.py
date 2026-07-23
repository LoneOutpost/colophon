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
