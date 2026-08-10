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


def _book(ctx, folder, *, title=None, tprov="tag", authors=None, aprov="tag"):
    b = BookUnit.new(source_folder=folder)
    if title is not None:
        b.title = title
        b.provenance["title"] = tprov
    if authors is not None:
        b.authors = authors
        b.provenance["authors"] = aprov
    ctx.books.upsert(b)
    return b


def test_plan_author_row_from_scheme(tmp_path):
    from colophon.controller import AppController
    ctx = _ctx(tmp_path)
    b = _book(ctx, tmp_path / "ingest" / "Alan Dean Foster" / "Flinx",
              title="Flinx", authors=["The End of the Matter (Flinx 03)"])
    rows = AppController(ctx).plan_repairs()
    author_rows = [r for r in rows if r.book_id == b.id and r.field == "author"]
    assert len(author_rows) == 1
    assert author_rows[0].after == "Alan Dean Foster"
    assert author_rows[0].kind == "title_shaped_author"
    ctx.close()


def test_plan_no_author_row_when_folder_unmatched(tmp_path):
    from colophon.controller import AppController
    ctx = _ctx(tmp_path)
    b = _book(ctx, tmp_path / "ingest" / "Foster.-.Flinx.-.End",
              title="End", authors=["The End of the Matter (Flinx 03)"])
    rows = AppController(ctx).plan_repairs()
    assert not [r for r in rows if r.book_id == b.id and r.field == "author"]
    ctx.close()


def test_plan_empty_for_clean_library(tmp_path):
    from colophon.controller import AppController
    ctx = _ctx(tmp_path)
    _book(ctx, tmp_path / "ingest" / "Stella Rimington" / "At Risk",
          title="At Risk", authors=["Stella Rimington"])
    assert AppController(ctx).plan_repairs() == []
    ctx.close()


def test_apply_repairs_commits_manual_and_undoes(tmp_path):
    from colophon.controller import AppController
    ctx = _ctx(tmp_path)
    b = _book(ctx, tmp_path / "ingest" / "Alan Dean Foster" / "Flinx",
              title="Flinx", authors=["The End of the Matter (Flinx 03)"])
    ctrl = AppController(ctx)
    rows = ctrl.plan_repairs()
    assert rows and rows[0].field == "author"

    batch = ctrl.apply_repairs(rows)

    healed = ctx.books.get(b.id)
    assert healed.authors == ["Alan Dean Foster"]
    assert healed.provenance["authors"] == "manual"   # survives a rescan
    assert ctrl.plan_repairs() == []                  # idempotent

    ctrl.undo(batch)
    assert ctx.books.get(b.id).authors == ["The End of the Matter (Flinx 03)"]
    ctx.close()
