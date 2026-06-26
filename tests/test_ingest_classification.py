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


def test_scan_fills_series_sequence_for_untagged_single_in_series(tmp_path):
    book_dir = tmp_path / "Sally MacKenzie" / "Duchess of Love"
    book_dir.mkdir(parents=True)
    (book_dir / "Duchess of Love (Duchess of Love Trilogy 0.5).mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(book_dir))
    assert book.content_kind is ContentKind.SINGLE
    assert book.series and book.series[0].name == "Duchess of Love Trilogy"
    assert book.series[0].sequence == 0.5


def test_scan_uses_filename_title_for_single_book_in_author_folder(tmp_path):
    # Folder name is the author; the one file is the actual book.
    author = tmp_path / "Srini Pillay"
    author.mkdir()
    (author / "Tinker Dabble Doodle.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(author))
    assert book.title == "Tinker Dabble Doodle"
    assert book.authors == ["Srini Pillay"]


def test_scan_keeps_folder_title_when_folder_matches_filename(tmp_path):
    # Folder name relates to the title -> a proper title folder; keep the folder name.
    book_dir = tmp_path / "7th Sigma"
    book_dir.mkdir()
    (book_dir / "7thSigmaUnabridgedPart1_ep6.mp3").write_bytes(b"")
    (book_dir / "7thSigmaUnabridgedPart2_ep6.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(book_dir))
    assert book.title == "7th Sigma"  # not the "7th Sigma Unabridged Part" residue


def test_scan_records_filename_provenance_for_inferred_fields(tmp_path):
    from colophon.core.models import Provenance
    author = tmp_path / "Srini Pillay"
    author.mkdir()
    (author / "Tinker Dabble Doodle.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(author))
    assert book.provenance.get("title") == Provenance.FILENAME.value
    assert book.provenance.get("authors") == Provenance.FILENAME.value


def test_scan_records_filename_provenance_for_series(tmp_path):
    from colophon.core.models import Provenance
    d = tmp_path / "Sally MacKenzie" / "Duchess of Love"
    d.mkdir(parents=True)
    (d / "Duchess of Love (Duchess of Love Trilogy 0.5).mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    scan_ingest(repo, tmp_path, template="$Title", directory_scheme="")
    book = repo.get(BookUnit.id_for(d))
    assert book.provenance.get("series") == Provenance.FILENAME.value


def test_plan_scan_reports_progress_per_folder(tmp_path):
    from colophon.services.ingest import plan_scan
    for name in ("A", "B", "C"):
        d = tmp_path / name
        d.mkdir()
        (d / f"{name}.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    calls: list[tuple[int, int, str]] = []
    plan_scan(repo, tmp_path, template="$Title", directory_scheme="",
              progress=lambda done, total, name: calls.append((done, total, name)))
    assert [c[0] for c in calls] == [1, 2, 3]          # done rises one per folder
    assert all(c[1] == 3 for c in calls)               # total is the folder count
    assert {c[2] for c in calls} == {"A", "B", "C"}    # each folder reported
