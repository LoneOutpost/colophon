from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.core.models import BookUnit, SeriesRef
from colophon.services.catalog import apply_catalog_mapping
from colophon.services.undo import undo_batch


def _ctx(tmp_path):
    return AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))


def test_rename_and_dedupe(tmp_path):
    ctx = _ctx(tmp_path)
    b1 = BookUnit.new(source_folder=tmp_path / "1")
    b1.genres = ["Sci-Fi", "Fantasy"]
    ctx.books.upsert(b1)
    b2 = BookUnit.new(source_folder=tmp_path / "2")
    b2.genres = ["Sci-Fi"]
    ctx.books.upsert(b2)
    affected, batch_id = apply_catalog_mapping(ctx.books, ctx.history, "genre", {"Sci-Fi": "Science Fiction"})
    assert set(affected) == {b1.id, b2.id}
    assert batch_id is not None
    assert ctx.books.get(b1.id).genres == ["Science Fiction", "Fantasy"]
    assert ctx.books.get(b2.id).genres == ["Science Fiction"]
    ctx.close()


def test_merge_dedupe(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "1")
    b.genres = ["SciFi", "sci fi", "Fantasy"]
    ctx.books.upsert(b)
    apply_catalog_mapping(ctx.books, ctx.history, "genre", {"SciFi": "Science Fiction", "sci fi": "Science Fiction"})
    assert ctx.books.get(b.id).genres == ["Science Fiction", "Fantasy"]
    ctx.close()


def test_delete_and_undo(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "1")
    b.genres = ["Junk", "Fantasy"]
    ctx.books.upsert(b)
    _, batch_id = apply_catalog_mapping(ctx.books, ctx.history, "genre", {"Junk": None})
    assert ctx.books.get(b.id).genres == ["Fantasy"]
    undo_batch(ctx.books, ctx.history, batch_id)
    assert ctx.books.get(b.id).genres == ["Junk", "Fantasy"]
    ctx.close()


def test_series_preserves_sequence(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "1")
    b.series = [SeriesRef(name="Stormlight", sequence=2.0)]
    ctx.books.upsert(b)
    apply_catalog_mapping(ctx.books, ctx.history, "series", {"Stormlight": "The Stormlight Archive"})
    out = ctx.books.get(b.id)
    assert out.series[0].name == "The Stormlight Archive"
    assert out.series[0].sequence == 2.0
    ctx.close()


def test_no_change_returns_empty(tmp_path):
    ctx = _ctx(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "1")
    b.genres = ["Fantasy"]
    ctx.books.upsert(b)
    affected, batch_id = apply_catalog_mapping(ctx.books, ctx.history, "genre", {"Nonexistent": "X"})
    assert affected == [] and batch_id is None
    ctx.close()
