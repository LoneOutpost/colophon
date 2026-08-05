from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _untagged(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # no embedded tags -> identity must come from folder + filename


def _ctrl(tmp_path):
    ingest = tmp_path / "audio"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
                                   scan_paths=[ingest]))
    return ctx, AppController(ctx), ingest


def test_track_of_total_folder_identifies_as_one_book_by_parent_author(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Aldous Huxley" / "Crome Yellow"
    for i in range(1, 13):
        _untagged(folder / f"{i:02d}-12 - Chrome Yellow - Aldous Huxley.mp3")
    ctrl.scan([ingest])

    ids = list(ctx.books.ids_in_folder(folder))
    books = [ctx.books.get(i) for i in ids]
    assert len(books) == 1, f"expected 1 book, got {len(books)}"
    b = books[0]
    assert b.title in ("Chrome Yellow", "Crome Yellow"), b.title
    assert b.authors == ["Aldous Huxley"], b.authors
    ctx.close()


def test_series_book_prefix_folder_sets_title_series_sequence(tmp_path):
    # A "(Series Book #N) Title" folder identifies title + series + sequence from the folder name.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = (ingest / "MC Beaton" / "A Hamish Macbeth Mystery"
              / "(A Hamish Macbeth Mystery Book #1) Death of a Gossip")
    for i in range(1, 5):
        _untagged(folder / f"(A Hamish Macbeth Mystery Book #1) Death of a Gossip - Part {i:02d}.mp3")
    ctrl.scan([ingest])

    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)]
    assert len(books) == 1
    b = books[0]
    assert b.title == "Death of a Gossip"
    assert [s.name for s in b.series] == ["A Hamish Macbeth Mystery"]
    assert b.series[0].sequence == 1.0
    ctx.close()


def test_leading_compound_glued_folder_is_one_titled_book(tmp_path):
    # "01-04-Keith Douglass - [Carrier #02] - Viper Strike": four parts of one book; the folder
    # title wins once the parts cluster. (Author/sequence are the deferred node-classification slice.)
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Keith Douglass" / "Carrier" / "(Carrier Book #2) Viper Strike"
    for i in range(1, 5):
        _untagged(folder / f"{i:02d}-04-Keith Douglass - [Carrier #02] - Viper Strike.mp3")
    ctrl.scan([ingest])

    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)]
    assert len(books) == 1, f"expected 1 book, got {len(books)}"
    assert books[0].title == "Viper Strike", books[0].title
    assert books[0].provenance.get("title") == "directory"
    ctx.close()


def test_trailing_part_token_folder_is_one_titled_book(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Isaac Asimov" / "Foundation" / "(Foundation Book #7) Foundation and Earth"
    for i in range(1, 14):
        _untagged(folder / f"07-Foundation and Earth - {i:02d}a.mp3")
        _untagged(folder / f"07-Foundation and Earth - {i:02d}b.mp3")
    ctrl.scan([ingest])

    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)]
    assert len(books) == 1, f"expected 1 book, got {len(books)}"
    assert books[0].title == "Foundation and Earth", books[0].title
    ctx.close()


def test_full_scan_and_reidentify_agree_for_clustered_book(tmp_path):
    # The user's "targeted rescan fixes it, full scan re-breaks" was not a real divergence:
    # both paths run the same clustering primitive and must agree.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Keith Douglass" / "Carrier" / "(Carrier Book #2) Viper Strike"
    for i in range(1, 5):
        _untagged(folder / f"{i:02d}-04-Keith Douglass - [Carrier #02] - Viper Strike.mp3")
    ctrl.scan([ingest])
    after_scan = [(b.title, len(b.source_files))
                  for b in (ctx.books.get(i) for i in ctx.books.ids_in_folder(folder))]

    ctrl.reidentify([ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)])
    after_reid = [(b.title, len(b.source_files))
                  for b in (ctx.books.get(i) for i in ctx.books.ids_in_folder(folder))]
    assert after_scan == after_reid == [("Viper Strike", 4)]
    ctx.close()
