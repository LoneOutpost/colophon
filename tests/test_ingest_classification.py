import logging
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, ContentKind
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
    # The multi-book author folder splits into one SINGLE leaf per work; the container
    # itself is not persisted (the graph is the source of truth).
    assert repo.get(BookUnit.id_for(author)) is None
    books = repo.list_all()
    assert {b.title for b in books} == {"Legion", "Elantris"}
    assert all(b.content_kind is ContentKind.SINGLE for b in books)


def test_scan_does_not_crash_on_classification(tmp_path):
    d = tmp_path / "Legion"
    d.mkdir()
    (d / "Legion.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    units = scan_ingest(repo, tmp_path, template="$Title", directory_scheme="$Author/$Title")
    assert len(units) == 1


def test_categorize_logs_content_kind_and_signals_at_debug(tmp_path, caplog):
    author = tmp_path / "Brandon Sanderson"
    author.mkdir()
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="colophon.services.ingest"):
        scan_ingest(repo, tmp_path, template="$Title", directory_scheme="$Author/$Title")
    categorize = [r for r in caplog.records if "CATEGORIZE" in r.getMessage()]
    assert categorize, "expected a CATEGORIZE debug record"
    msg = categorize[0].getMessage()
    assert "content_kind=" in msg
    assert "signals=" in msg


def test_scan_emits_nothing_above_debug(tmp_path, caplog):
    author = tmp_path / "Brandon Sanderson"
    author.mkdir()
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    with caplog.at_level(logging.INFO, logger="colophon.services.ingest"):
        scan_ingest(repo, tmp_path, template="$Title", directory_scheme="$Author/$Title")
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []
