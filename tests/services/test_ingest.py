import json
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookState, Provenance
from colophon.services.ingest import scan_ingest


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_scan_ingest_persists_book_units(tmp_path: Path):
    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    f = dune / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Frank Herbert"]))
    id3.save(f)

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="%author% - %title%")

    assert len(units) == 1
    book = units[0]
    assert book.state == BookState.DETECTED
    assert book.title == "Dune"  # from directory name
    assert book.provenance["title"] == Provenance.DIRECTORY.value
    assert book.authors == ["Frank Herbert"]  # from embedded TPE1
    assert book.provenance["authors"] == Provenance.TAG.value
    assert len(book.source_files) == 1
    # persisted and retrievable
    assert repo.get(book.id) is not None


def test_first_file_drives_metadata_with_numeric_sort(tmp_path: Path):
    ingest = tmp_path / "ingest"
    book_dir = ingest / "Book"
    book_dir.mkdir(parents=True)
    (book_dir / "2.mp3").write_bytes(b"")
    (book_dir / "10.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="%author% - %title%")

    assert len(units) == 1
    book = units[0]
    assert len(book.source_files) == 2
    # Scanner natural-sorts files, so the lowest track number drives metadata.
    assert book.source_files[0].path.name == "2.mp3"


def test_non_matching_filename_falls_back_to_dir_and_tags(tmp_path: Path):
    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "weird_name_no_delimiters.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="%author% - %title%")

    assert len(units) == 1
    book = units[0]
    assert book.title == "Dune"


def test_scan_ingest_is_idempotent(tmp_path: Path):
    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    scan_ingest(repo, ingest, template="%author% - %title%")
    scan_ingest(repo, ingest, template="%author% - %title%")
    assert len(repo.list_all()) == 1  # same folder -> same id -> upsert


def test_scan_ingest_uses_sidecar_for_series(tmp_path):
    ingest = tmp_path / "ingest"
    book_dir = ingest / "Dirk Gently"
    book_dir.mkdir(parents=True)
    (book_dir / "01.mp3").write_bytes(b"")
    (book_dir / "metadata.json").write_text(json.dumps({
        "title": "Dirk Gently", "authors": ["Douglas Adams"],
        "narrators": ["Douglas Adams"], "series": ["Dirk Gently #1"], "publishedYear": "1987",
    }))

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="%author% - %title%")
    book = units[0]
    assert book.series[0].name == "Dirk Gently"
    assert book.series[0].sequence == 1.0
    assert book.narrators == ["Douglas Adams"]
    assert book.publish_year == 1987
    assert book.provenance["series"] == "sidecar"
