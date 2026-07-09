from pathlib import Path

from colophon.adapters.lazylibrarian import PathPatterns
from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookState, BookUnit
from colophon.core.pathscheme import build_target_path
from colophon.services.organize import organize_book, organize_book_parts


def _repo(tmp_path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _book(tmp_path) -> BookUnit:
    b = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    b.title = "Dune"
    b.authors = ["Frank Herbert"]
    return b


def _make_m4b(tmp_path) -> Path:
    src = tmp_path / "staging" / "out.m4b"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"fake m4b bytes")
    return src


def test_organize_moves_and_marks_organized(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    m4b = _make_m4b(tmp_path)
    library = tmp_path / "library"
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")

    target = build_target_path(library, pats, book)
    result = organize_book(repo, book, m4b, target=target)

    assert result.moved is True and result.collision is False
    expected = library / "Frank Herbert" / "Dune" / "Dune.m4b"
    assert result.target_path == expected
    assert expected.exists() and not m4b.exists()  # moved, not copied
    persisted = repo.get(book.id)
    assert persisted.state == BookState.ORGANIZED
    assert persisted.output_path == expected


def test_organize_detects_collision_and_does_not_overwrite(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    m4b = _make_m4b(tmp_path)
    library = tmp_path / "library"
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")
    # pre-create the destination
    dest = library / "Frank Herbert" / "Dune" / "Dune.m4b"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"existing")

    target = build_target_path(library, pats, book)
    result = organize_book(repo, book, m4b, target=target)

    assert result.collision is True and result.moved is False
    assert dest.read_bytes() == b"existing"   # untouched
    assert m4b.exists()                        # source not moved
    assert repo.get(book.id).state != BookState.ORGANIZED


def test_organize_move_failure_returns_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    m4b = _make_m4b(tmp_path)
    library = tmp_path / "library"
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("colophon.services.organize.shutil.move", _boom)

    target = build_target_path(library, pats, book)
    result = organize_book(repo, book, m4b, target=target)

    assert result.error is not None and result.moved is False
    assert repo.get(book.id).state != BookState.ORGANIZED
    # the 0-byte placeholder reserved with O_EXCL must be cleaned up
    target = library / "Frank Herbert" / "Dune" / "Dune.m4b"
    assert not target.exists()


def test_organize_does_not_write_destination_datafile(tmp_path):
    # colophon does not write metadata.json — that is AudiobookShelf's domain.
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    book.authors = ["Frank Herbert"]
    repo.upsert(book)
    m4b = _make_m4b(tmp_path)
    library = tmp_path / "library"
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")

    target = build_target_path(library, pats, book)
    result = organize_book(repo, book, m4b, target=target)

    assert result.moved is True
    assert not (result.target_path.parent / "metadata.json").exists()


def _make_source(tmp_path, name: str, data: bytes) -> Path:
    p = tmp_path / "ingest" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_organize_parts_copies_all_and_marks_organized(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    s1 = _make_source(tmp_path, "a.mp3", b"one")
    s2 = _make_source(tmp_path, "b.mp3", b"two")
    folder = tmp_path / "library" / "Frank Herbert" / "Dune"
    pairs = [(s1, folder / "Dune - Part 01 of 02.mp3"), (s2, folder / "Dune - Part 02 of 02.mp3")]

    result = organize_book_parts(repo, book, pairs, delete_sources=False)

    assert result.moved is True and result.collision is False
    assert (folder / "Dune - Part 01 of 02.mp3").read_bytes() == b"one"
    assert (folder / "Dune - Part 02 of 02.mp3").read_bytes() == b"two"
    assert s1.exists() and s2.exists()  # copy, not move
    persisted = repo.get(book.id)
    assert persisted.output_path == folder  # folder is the resting location


def test_organize_parts_deletes_sources_when_requested(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    s1 = _make_source(tmp_path, "a.mp3", b"one")
    folder = tmp_path / "library" / "Frank Herbert" / "Dune"
    pairs = [(s1, folder / "Dune.mp3")]

    organize_book_parts(repo, book, pairs, delete_sources=True)

    assert (folder / "Dune.mp3").read_bytes() == b"one"
    assert not s1.exists()  # deleted only after verified copy


def test_organize_parts_collision_leaves_everything_untouched(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    repo.upsert(book)
    s1 = _make_source(tmp_path, "a.mp3", b"one")
    s2 = _make_source(tmp_path, "b.mp3", b"two")
    folder = tmp_path / "library" / "Frank Herbert" / "Dune"
    folder.mkdir(parents=True)
    (folder / "Dune - Part 02 of 02.mp3").write_bytes(b"existing")
    pairs = [(s1, folder / "Dune - Part 01 of 02.mp3"), (s2, folder / "Dune - Part 02 of 02.mp3")]

    result = organize_book_parts(repo, book, pairs, delete_sources=True)

    assert result.collision is True and result.moved is False
    assert not (folder / "Dune - Part 01 of 02.mp3").exists()  # rolled back
    assert (folder / "Dune - Part 02 of 02.mp3").read_bytes() == b"existing"  # untouched
    assert s1.exists() and s2.exists()  # sources kept on failure
