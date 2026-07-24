from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _scan(tmp_path, folder_name, files):
    ingest = tmp_path / "ingest"
    d = ingest / folder_name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    AppController(ctx).scan()
    return ctx


def test_standalone_title_folder_gets_no_spurious_author(tmp_path):
    ctx = _scan(tmp_path, "1981 - Danse Macabre (Nonfiction - read by William Dufris)",
                ["Danse Macabre.mp3"])
    book = next(iter(ctx.books.list_all()))
    assert book.authors == []                       # was ["ingest"]
    assert book.publish_year == 1981
    assert book.narrators == ["William Dufris"]
    assert book.title == "Danse Macabre"            # genre qualifier "(Nonfiction)" stripped
    ctx.close()


def test_part_structured_title_takes_its_title_from_the_folder(tmp_path):
    ctx = _scan(tmp_path, "1979 - The Dead Zone", ["Chapter 01.mp3", "Chapter 02.mp3"])
    book = next(iter(ctx.books.list_all()))
    assert book.title == "The Dead Zone"
    assert book.series == []
    ctx.close()


def test_real_author_folder_still_identifies(tmp_path):
    ctx = _scan(tmp_path / "root", "Stephen King", ["Cujo.mp3"])
    book = next(iter(ctx.books.list_all()))
    assert book.authors == ["Stephen King"]
    assert book.title == "Cujo"
    ctx.close()
