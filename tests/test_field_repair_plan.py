"""Field-repair Slice 2: plan_repairs + apply_repairs (previewed trust-inverting repairs)."""
from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.core.models import BookUnit, Provenance


def _ctx(tmp_path, scheme="$Author/$Title"):
    return AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"], directory_scheme=scheme))


def test_bulk_apply_fields_one_batch_across_books(tmp_path):
    # apply_repairs relies on bulk_apply_fields: many books, one undoable MANUAL batch.
    from colophon.services.editing import bulk_apply_fields
    ctx = _ctx(tmp_path)
    a = BookUnit.new(source_folder=tmp_path / "ingest" / "A")
    a.title = "Old A"
    b = BookUnit.new(source_folder=tmp_path / "ingest" / "B")
    b.title = "Old B"
    ctx.books.upsert(a)
    ctx.books.upsert(b)

    batch = bulk_apply_fields(
        ctx.books, ctx.history,
        [(a, {"title": "New A"}, Provenance.MANUAL.value),
         (b, {"title": "New B"}, Provenance.MANUAL.value)],
    )

    assert ctx.books.get(a.id).title == "New A"
    assert ctx.books.get(a.id).provenance["title"] == Provenance.MANUAL.value
    assert ctx.books.get(b.id).title == "New B"
    # one batch id covers both books
    from colophon.services.undo import undo_batch
    undo_batch(ctx.books, ctx.history, batch)
    assert ctx.books.get(a.id).title == "Old A"
    assert ctx.books.get(b.id).title == "Old B"
    ctx.close()
