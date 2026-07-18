"""Album/Title tag routing for multi-book folders.

Across a real audiobook library the Album tag very often holds the *series* while the Title tag
holds the book's title (e.g. Album "Thrawn Ascendancy" / Title "Chaos Rising"). A single-file book
must take its title from the Title tag, not the series-bearing Album, and record honest provenance.
"""

from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import scan_ingest


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _write(path: Path, *, title=None, album=None, artist=None) -> None:
    path.write_bytes(b"")
    tags = ID3()
    if artist is not None:
        tags.add(TPE1(encoding=3, text=[artist]))
    if album is not None:
        tags.add(TALB(encoding=3, text=[album]))
    if title is not None:
        tags.add(TIT2(encoding=3, text=[title]))
    tags.save(path)


def test_single_book_title_comes_from_title_tag_not_series_album(tmp_path):
    ingest = tmp_path / "ingest"
    dump = ingest / "STAR WARS"
    dump.mkdir(parents=True)
    # A multi-book dump folder so each file fans out into its own leaf.
    _write(dump / "Chaos Rising (Thrawn Ascendancy 1).mp3",
           title="Chaos Rising", album="Thrawn Ascendancy", artist="Timothy Zahn")
    _write(dump / "Thrawn (Thrawn 1).mp3",
           title="Thrawn", album="Thrawn", artist="Timothy Zahn")

    books = scan_ingest(_repo(tmp_path), ingest, template="$Author - $Title")
    cr = next(b for b in books if b.source_files[0].path.name.startswith("Chaos Rising"))

    assert cr.title == "Chaos Rising"                       # the Title tag, not the album
    assert [s.name for s in cr.series] == ["Thrawn Ascendancy"]   # album's value lands as the series
    assert cr.provenance["title"] == "tag"                  # it came from a tag, not a filename guess
