from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, ContentKind, FolderKind
from colophon.services.ingest import scan_ingest


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_scan_classifies_multi_author_folder(tmp_path):
    author = tmp_path / "Brandon Sanderson"
    author.mkdir()
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="$Author/$Title")
    book = repo.get(BookUnit.id_for(author))
    # detected_works is only populated by the wiring (defaults to []), so it is
    # the discriminator that makes this test fail before the wiring exists.
    assert len(book.detected_works) == 2
    assert book.folder_kind in (FolderKind.AUTHOR, FolderKind.UNDETERMINED)
    assert book.content_kind in (ContentKind.MULTI, ContentKind.UNKNOWN)


def test_scan_does_not_crash_on_classification(tmp_path):
    d = tmp_path / "Legion"
    d.mkdir()
    (d / "Legion.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    units = scan_ingest(repo, tmp_path, template="$Title", directory_scheme="$Author/$Title")
    assert len(units) == 1
