"""Contract test for identify_hard / identify_weak / run_identify.

Verifies that the split of run_identify into two stages preserves the
end-to-end result: a folder named '1981 - Cujo (read by Lorna Raver)'
with one untagged MP3 must identify as title=Cujo, year=1981,
narrators=["Lorna Raver"].

Also covers role-driven attribute() behaviour (Task 6):
- role="title"  → folder is the title; no author adopted from the folder name
- role="author" → folder names the author; title comes from the filename label
"""

from colophon.adapters.audio import probe_audio_file
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_cluster import cluster
from colophon.core.filename_parser import compile_template
from colophon.core.models import BookUnit, ContentKind
from colophon.services.identify import identify_hard, identify_weak, run_identify


def _make_book(tmp_path):
    """Build a minimal BookUnit from a '1981 - Cujo (read by Lorna Raver)' folder."""
    root = tmp_path / "lib"
    folder = root / "1981 - Cujo (read by Lorna Raver)"
    folder.mkdir(parents=True)
    mp3 = folder / "01_cujo.mp3"
    mp3.write_bytes(b"")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(mp3)]
    book.content_kind = ContentKind.SINGLE
    return book, root


def _pattern_and_scheme():
    return compile_template("$Author - $Title"), parse_scheme("")


def test_run_identify_cujo_end_to_end(tmp_path):
    """run_identify on an untagged folder produces the correct identity."""
    book, root = _make_book(tmp_path)
    pattern, scheme = _pattern_and_scheme()

    run_identify(book, root=root, pattern=pattern, scheme=scheme)

    assert book.title == "Cujo"
    assert book.publish_year == 1981
    assert book.narrators == ["Lorna Raver"]


def test_identify_hard_then_weak_equals_run_identify(tmp_path):
    """Calling identify_hard then identify_weak produces the same result as run_identify."""
    book_a, root_a = _make_book(tmp_path / "a")
    book_b, root_b = _make_book(tmp_path / "b")
    pattern, scheme = _pattern_and_scheme()

    run_identify(book_a, root=root_a, pattern=pattern, scheme=scheme)

    evidence = identify_hard(book_b, root=root_b, pattern=pattern, scheme=scheme)
    identify_weak(book_b, evidence)

    assert book_b.title == book_a.title
    assert book_b.publish_year == book_a.publish_year
    assert book_b.narrators == book_a.narrators
    assert book_b.authors == book_a.authors


def test_identify_hard_returns_evidence(tmp_path):
    """identify_hard returns an Evidence object that identify_weak can consume."""
    book, root = _make_book(tmp_path)
    pattern, scheme = _pattern_and_scheme()

    from colophon.services.identify import Evidence
    evidence = identify_hard(book, root=root, pattern=pattern, scheme=scheme)
    assert isinstance(evidence, Evidence)

    identify_weak(book, evidence)
    assert book.title == "Cujo"


def test_attribute_title_role_takes_no_author_from_the_folder(tmp_path):
    """A title-role folder never adopts its own name as an author (the ingest/folder-as-author bug)."""
    root = tmp_path / "lib"
    folder = root / "1981 - Danse Macabre (read by William Dufris)"
    folder.mkdir(parents=True)
    mp3 = folder / "danse_macabre.mp3"
    mp3.write_bytes(b"")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(mp3)]
    book.content_kind = ContentKind.SINGLE

    pattern, scheme = _pattern_and_scheme()
    ev = identify_hard(book, root=root, pattern=pattern, scheme=scheme)
    identify_weak(book, ev, role="title")

    assert book.authors == []


def test_attribute_author_role_sets_author_and_filename_title(tmp_path):
    """An author-role folder: author = folder name, title from the filename label."""
    root = tmp_path / "lib"
    folder = root / "Stephen King"
    folder.mkdir(parents=True)
    mp3 = folder / "Cujo.mp3"
    mp3.write_bytes(b"")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(mp3)]
    book.content_kind = ContentKind.SINGLE
    # Populate detected_works the way the classify phase does in the real pipeline.
    book.detected_works = cluster([mp3]).detected_works

    pattern, scheme = _pattern_and_scheme()
    ev = identify_hard(book, root=root, pattern=pattern, scheme=scheme)
    identify_weak(book, ev, role="author")

    assert book.authors == ["Stephen King"]
    assert book.title == "Cujo"
