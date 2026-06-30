import json
from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookState, BookUnit
from colophon.core.pathscheme import build_target_path
from colophon.services.organize import organize_book


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
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")

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
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")
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
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")

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


def test_organize_writes_destination_sidecar(tmp_path):
    repo = _repo(tmp_path)
    book = _book(tmp_path)
    book.authors = ["Frank Herbert"]
    repo.upsert(book)
    m4b = _make_m4b(tmp_path)
    library = tmp_path / "library"
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")

    target = build_target_path(library, pats, book)
    result = organize_book(repo, book, m4b, target=target)

    assert result.moved is True
    sidecar = result.target_path.parent / "metadata.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["title"] == "Dune"
    assert data["authors"] == ["Frank Herbert"]
