
from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _scanned_book(tmp_path, template="$Author - $Title"):
    """A scanned single-book folder named 'YYYY - Title', returning (ctx, controller, book)."""
    ingest = tmp_path / "ingest"
    d = ingest / "1981 - Cujo (read by Lorna Raver)"
    d.mkdir(parents=True)
    (d / "1981 - Cujo (read by Lorna Raver).mp3").write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[ingest], filename_template=template))
    c = AppController(ctx)
    c.scan()
    book = next(b for b in ctx.books.list_all() if b.source_folder == d)
    return ctx, c, book


def test_default_template_no_longer_makes_the_year_the_author(tmp_path):
    # The numeric-author guard: even the default $Author - $Title never yields a year author; the
    # year is captured as the publish year instead.
    ctx, _c, book = _scanned_book(tmp_path)
    assert book.authors != ["1981"]
    assert book.publish_year == 1981
    ctx.close()


def test_reidentify_clears_weak_fields_and_rederives_with_new_pattern(tmp_path):
    ctx, c, book = _scanned_book(tmp_path)
    # inject a stale, folder/filename-derived author to prove re-identify clears + re-derives it
    book.authors = ["Stale Guess"]
    book.provenance["authors"] = "filename"
    ctx.books.upsert(book)

    c.reidentify([book], template="$PubYear - $Title")

    reloaded = ctx.books.get(book.id)
    assert reloaded.authors == []                 # stale weak author cleared; new pattern names none
    assert reloaded.publish_year == 1981          # $PubYear captured it
    assert "$PubYear - $Title" in ctx.config.recent_filename_templates
    assert ctx.config.filename_template == "$Author - $Title"  # global default unchanged
    ctx.close()


def test_reidentify_preserves_a_hard_author(tmp_path):
    ctx, c, book = _scanned_book(tmp_path)
    book.authors = ["Stephen King"]
    book.provenance["authors"] = "manual"
    ctx.books.upsert(book)

    c.reidentify([book], template="$PubYear - $Title")

    assert ctx.books.get(book.id).authors == ["Stephen King"]  # manual value untouched
    ctx.close()


def test_reidentify_applies_a_node_reclassification(tmp_path):
    # reidentify now runs the resolving walk, so a manual reclassify of the folder feeds it too.
    ctx, c, book = _scanned_book(tmp_path)
    c.set_node_classification(book.source_folder.parent, "author", "Reclassified Author")

    c.reidentify([book])

    assert ctx.books.get(book.id).authors == ["Reclassified Author"]
    ctx.close()
