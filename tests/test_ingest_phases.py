from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import state_of
from colophon.services.ingest import scan_ingest


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_scan_marks_local_phases_fresh(tmp_path):
    d = tmp_path / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(d))
    assert state_of(book, Phase.SEARCH) is PhaseState.FRESH
    assert state_of(book, Phase.CATEGORIZE) is PhaseState.FRESH
    assert state_of(book, Phase.IDENTIFY) is PhaseState.FRESH
    assert state_of(book, Phase.MATCH) is PhaseState.PENDING
