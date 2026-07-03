from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_records import book_records
from colophon.core.models import BookUnit, SeriesRef


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


def _seed(ctx, tmp_path, books) -> None:
    """Persist `books` and populate the maintained graph for them (the entity records a
    scan would lay down), so `library_tree` — which now reads `ctx.library_graph` — sees
    them with their exact fields, without the scan/identify pipeline's fallbacks."""
    for b in books:
        ctx.books.upsert(b)
    nodes, edges = book_records(books, root=tmp_path)
    ctx.library_graph.replace_root(str(tmp_path), nodes, edges)


def test_library_tree_groups_authors_series_and_needs_id(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book(tmp_path, "Way of Kings", author="Brandon Sanderson", series="Stormlight", seq=1.0)
    b = _book(tmp_path, "Words of Radiance", author="Brandon Sanderson", series="Stormlight", seq=2.0)
    standalone = _book(tmp_path, "Warbreaker", author="Brandon Sanderson")
    mystery = _book(tmp_path, "mystery")  # no author, no series
    _seed(ctx, tmp_path, [a, b, standalone, mystery])

    tree = AppController(ctx).library_tree()
    assert [bk.id for bk in tree.needs_id] == [mystery.id]
    author = next(n for n in tree.authors if n.name == "Brandon Sanderson")
    series = next(s for s in author.series if s.name == "Stormlight")
    assert [bk.title for bk in series.books] == ["Way of Kings", "Words of Radiance"]  # by sequence
    assert [bk.title for bk in author.standalone] == ["Warbreaker"]
    ctx.close()


def test_library_tree_author_with_series_and_standalone_and_series_only(tmp_path):
    ctx = _ctx(tmp_path)
    # An author who has both a series book and a standalone book.
    series_book = _book(tmp_path, "Elantris Saga", author="Brandon Sanderson", series="Elantris", seq=1.0)
    standalone = _book(tmp_path, "Warbreaker", author="Brandon Sanderson")
    # A book with a series but NO author -> keyed under the series name as its own author node.
    series_only = _book(tmp_path, "Mistborn One", series="Mistborn", seq=1.0)
    created = [series_book, standalone, series_only]
    _seed(ctx, tmp_path, created)

    tree = AppController(ctx).library_tree()

    # Mixed author: has its series book under the right series, and its standalone.
    author = next(n for n in tree.authors if n.name == "Brandon Sanderson")
    elantris = next(s for s in author.series if s.name == "Elantris")
    assert [bk.id for bk in elantris.books] == [series_book.id]
    assert [bk.id for bk in author.standalone] == [standalone.id]

    # Series-without-author book appears under an author node named after the series.
    series_author = next(n for n in tree.authors if n.name == "Mistborn")
    mistborn = next(s for s in series_author.series if s.name == "Mistborn")
    assert [bk.id for bk in mistborn.books] == [series_only.id]

    # No book appears twice across the whole tree.
    all_ids: list[str] = [bk.id for bk in tree.needs_id]
    for node in tree.authors:
        for s in node.series:
            all_ids.extend(bk.id for bk in s.books)
        all_ids.extend(bk.id for bk in node.standalone)
    assert len(all_ids) == len(set(all_ids))
    assert len(all_ids) == len(created)
    ctx.close()


def test_library_tree_empty(tmp_path):
    ctx = _ctx(tmp_path)
    tree = AppController(ctx).library_tree()
    assert tree.needs_id == [] and tree.authors == []
    ctx.close()


def test_library_tree_is_memoized_between_reads(tmp_path):
    ctx = _ctx(tmp_path)
    _seed(ctx, tmp_path, [_book(tmp_path, "A", author="Author One")])
    ctrl = AppController(ctx)
    assert ctrl.library_tree() is ctrl.library_tree()  # same object: not rebuilt per read
    ctx.close()


def test_library_tree_memo_invalidates_on_book_write(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book(tmp_path, "A", author="Author One")
    _seed(ctx, tmp_path, [a])
    ctrl = AppController(ctx)
    first = ctrl.library_tree()
    b = _book(tmp_path, "B", author="Author Two")
    _seed(ctx, tmp_path, [a, b])  # upsert + replace_root -> books & graph generations bump
    second = ctrl.library_tree()
    assert second is not first
    assert {n.name for n in second.authors} >= {"Author One", "Author Two"}
    ctx.close()


def test_library_tree_memo_invalidates_on_alias_change(tmp_path):
    ctx = _ctx(tmp_path)
    _seed(ctx, tmp_path, [
        _book(tmp_path, "A", author="Anne Rice"),
        _book(tmp_path, "B", author="A. Rice"),
    ])
    ctrl = AppController(ctx)
    first = ctrl.library_tree()
    assert {n.name for n in first.authors} == {"Anne Rice", "A. Rice"}
    ctrl.set_entity_alias("author", "A. Rice", "Anne Rice")  # merge; resolved at read time
    second = ctrl.library_tree()
    assert second is not first
    assert "A. Rice" not in {n.name for n in second.authors}  # memo saw the alias change
    ctx.close()


def test_known_authors_memoized_and_invalidated(tmp_path):
    ctx = _ctx(tmp_path)
    _seed(ctx, tmp_path, [_book(tmp_path, "A", author="Author One")])
    ctrl = AppController(ctx)
    assert ctrl.known_authors() is ctrl.known_authors()  # memoized
    assert ctrl.known_authors() == ["Author One"]
    ctx.books.upsert(_book(tmp_path, "B", author="Author Two"))
    assert ctrl.known_authors() == ["Author One", "Author Two"]  # invalidated on write
    ctx.close()


def test_navigator_view_narrows_to_book_ids(tmp_path):
    ctx = _ctx(tmp_path)
    a = _book(tmp_path, "A", author="Brandon Sanderson")
    b = _book(tmp_path, "B", author="Robin Hobb")
    _seed(ctx, tmp_path, [a, b])
    ctrl = AppController(ctx)
    assert len(ctrl.navigator_view(None).authors) == 2        # None -> full tree
    view = ctrl.navigator_view({a.id})                        # narrow to one book's entities
    assert [n.name for n in view.authors] == ["Brandon Sanderson"]
    assert {bk.id for bk in view.all_books} == {a.id}         # nav's book set matches the filter
    ctx.close()


def test_list_directory_separates_dirs_audio_and_other(tmp_path):
    root = tmp_path / "Author"
    (root / "Mistborn").mkdir(parents=True)
    (root / "Legion.mp3").write_bytes(b"")
    (root / "Warbreaker.m4b").write_bytes(b"")
    (root / "readme.txt").write_bytes(b"")

    listing = AppController(_ctx(tmp_path)).list_directory(root)
    assert listing.path == root
    by_name = {e.name: e for e in listing.entries}
    assert by_name["Mistborn"].is_dir is True
    assert by_name["Legion.mp3"].is_audio is True and by_name["Legion.mp3"].is_dir is False
    assert by_name["Warbreaker.m4b"].is_audio is True
    assert by_name["readme.txt"].is_audio is False and by_name["readme.txt"].is_dir is False
    # dirs first, then files, each case-insensitive alphabetical
    names = [e.name for e in listing.entries]
    assert names == ["Mistborn", "Legion.mp3", "readme.txt", "Warbreaker.m4b"]


def test_list_directory_missing_path_is_empty(tmp_path):
    listing = AppController(_ctx(tmp_path)).list_directory(tmp_path / "nope")
    assert listing.entries == []
