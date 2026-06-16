from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
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
    # dirs first, then files, each alphabetical
    names = [e.name for e in listing.entries]
    assert names == ["Mistborn", "Legion.mp3", "Warbreaker.m4b", "readme.txt"]


def test_list_directory_missing_path_is_empty(tmp_path):
    listing = AppController(_ctx(tmp_path)).list_directory(tmp_path / "nope")
    assert listing.entries == []
