from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookState, BookUnit, SeriesRef


def _ctx(tmp_path) -> AppContext:
    return AppContext.create(Config(db_path=tmp_path / "db.sqlite"))


def _book(tmp_path, name, *, author=None, series=None, seq=None) -> BookUnit:
    b = BookUnit.new(source_folder=tmp_path / name)
    b.title = name
    if author:
        b.authors = [author]
    if series:
        b.series = [SeriesRef(name=series, sequence=seq)]
    return b


def test_library_tree_groups_authors_series_and_needs_id(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book(tmp_path, "Way of Kings", author="Brandon Sanderson", series="Stormlight", seq=1.0)
    b = _book(tmp_path, "Words of Radiance", author="Brandon Sanderson", series="Stormlight", seq=2.0)
    standalone = _book(tmp_path, "Warbreaker", author="Brandon Sanderson")
    mystery = _book(tmp_path, "mystery")  # no author, no series
    for x in (a, b, standalone, mystery):
        ctx.books.upsert(x)

    tree = AppController(ctx).library_tree()
    assert [bk.id for bk in tree.needs_id] == [mystery.id]
    author = next(n for n in tree.authors if n.name == "Brandon Sanderson")
    series = next(s for s in author.series if s.name == "Stormlight")
    assert [bk.title for bk in series.books] == ["Way of Kings", "Words of Radiance"]  # by sequence
    assert [bk.title for bk in author.standalone] == ["Warbreaker"]
    ctx.close()


def test_library_tree_empty(tmp_path):
    ctx = _ctx(tmp_path)
    tree = AppController(ctx).library_tree()
    assert tree.needs_id == [] and tree.authors == []
    ctx.close()
