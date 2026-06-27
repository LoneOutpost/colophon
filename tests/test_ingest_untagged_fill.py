from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import Provenance
from colophon.services.ingest import plan_scan


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_untagged_single_book_in_author_folder_gets_filename_title_and_folder_author(tmp_path):
    # Uploader/Author/<Title>.mp3 — a single untagged file loose in an author folder.
    folder = tmp_path / "TE_Audiobooks_S" / "Sammy Hagar"
    folder.mkdir(parents=True)
    (folder / "Red My Uncensored Life in Rock.mp3").write_bytes(b"")
    repo = _repo(tmp_path)
    plan = plan_scan(repo, tmp_path / "TE_Audiobooks_S", template="$Author - $Title")
    book = next(b for b in plan.units if b.source_folder == folder)
    assert book.title == "Red My Uncensored Life in Rock"      # filename, not folder name
    assert book.authors == ["Sammy Hagar"]                     # promoted from the folder
    assert book.provenance.get("title") == Provenance.FILENAME.value
    assert book.provenance.get("authors") == Provenance.FILENAME.value
