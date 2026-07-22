import json
from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2

from colophon.adapters.audio import probe_audio_file
from colophon.adapters.sidecar import DatafileSidecar
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import (
    BookUnit,
    ContentKind,
    DetectedWork,
    Finding,
    FindingCode,
    FindingSeverity,
    FolderKind,
    Provenance,
    SeriesRef,
)
from colophon.services.identify import (
    Evidence,
    attribute,
    gather,
    normalize,
    seed_series,
)


def test_gather_collects_evidence_and_vets_container_datafile(tmp_path):
    folder = tmp_path / "TE_Audiobooks_S" / "Sarah Graves"
    folder.mkdir(parents=True)
    (folder / "a.mp3").write_bytes(b"")
    (folder / "metadata.json").write_text(
        json.dumps({"title": "Sarah Graves", "authors": ["TE_Audiobooks_S"]}))
    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(folder / "a.mp3")]
    book.content_kind = ContentKind.MULTI

    ev = gather(book, root=tmp_path,
                pattern=compile_template("$Author - $Title"), scheme=parse_scheme(""))
    assert ev.first_path == folder / "a.mp3"
    assert ev.datafile is None


def test_seed_series_fills_from_cluster(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.content_kind = ContentKind.SINGLE
    book.detected_works = [DetectedWork(label="X", series="Dark Tower", sequence=1.0, files=[])]
    seed_series(book)
    assert book.series[0].name == "Dark Tower" and book.series[0].sequence == 1.0
    assert book.provenance["series"] == "filename"


def test_seed_series_keeps_existing_series(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.content_kind = ContentKind.SINGLE
    book.series = [SeriesRef(name="Existing")]
    book.detected_works = [DetectedWork(label="X", series="Dark Tower", sequence=1.0, files=[])]
    seed_series(book)
    assert book.series[0].name == "Existing"


def test_normalize_is_identity(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.title = "1982 - The Gunslinger (DT1 - original edition)"
    normalize(book)
    assert book.title == "1982 - The Gunslinger (DT1 - original edition)"


def test_attribute_sets_foster_container_author(tmp_path):
    folder = tmp_path / "Sarah Graves"
    book = BookUnit.new(source_folder=folder)
    book.content_kind = ContentKind.MULTI
    book.folder_kind = FolderKind.UNDETERMINED
    book.detected_works = [DetectedWork(label="A", files=[]), DetectedWork(label="B", files=[])]
    book.findings = [Finding(
        code=FindingCode.MULTI_IN_UNDETERMINED, severity=FindingSeverity.WARN, detail="x")]
    attribute(book, Evidence(first_path=folder / "a.mp3"))
    assert book.authors == ["Sarah Graves"]
    assert book.provenance["authors"] == "directory"


def test_attribute_is_noop_for_a_clean_single_book(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "Elantris")
    book.content_kind = ContentKind.SINGLE
    book.title = "Elantris"
    book.authors = ["Brandon Sanderson"]
    attribute(book, Evidence(first_path=tmp_path / "Elantris" / "01.mp3"))
    assert book.authors == ["Brandon Sanderson"] and book.title == "Elantris"


def test_normalize_cleans_directory_title(tmp_path):
    from colophon.core.models import Provenance
    book = BookUnit.new(source_folder=tmp_path / "x")
    book.title = "1982 - The Gunslinger (DT1 - original edition)"
    book.provenance["title"] = Provenance.DIRECTORY.value
    normalize(book)
    # Edition parenthetical is stripped; the leading year is intentionally KEPT — a stored title
    # never risks the ambiguous year-vs-numeric-title guess (that strip is query-only).
    assert book.title == "1982 - The Gunslinger"
    assert book.provenance["title"] == Provenance.DIRECTORY.value  # provenance unchanged


def test_normalize_leaves_tag_and_manual_titles_untouched(tmp_path):
    from colophon.core.models import Provenance
    messy = "1982 - The Gunslinger (DT1 - original edition)"
    tagged = BookUnit.new(source_folder=tmp_path / "t")
    tagged.title = messy
    tagged.provenance["title"] = Provenance.TAG.value
    manual = BookUnit.new(source_folder=tmp_path / "m")
    manual.title = messy
    manual.provenance["title"] = Provenance.MANUAL.value
    normalize(tagged)
    normalize(manual)
    assert tagged.title == messy
    assert manual.title == messy


def test_normalize_is_idempotent_and_clean_title_unchanged(tmp_path):
    from colophon.core.models import Provenance
    book = BookUnit.new(source_folder=tmp_path / "d")
    book.title = "Dune"
    book.provenance["title"] = Provenance.DIRECTORY.value
    normalize(book)
    assert book.title == "Dune"
    normalize(book)  # idempotent
    assert book.title == "Dune"


def test_drop_clears_datafile_fields_when_datafile_gone(tmp_path):
    from colophon.services.identify import drop_orphaned_datafile_fields

    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.title = "Orphaned Title"
    book.authors = ["Orphaned Author"]
    book.provenance["title"] = Provenance.DATAFILE.value
    book.provenance["authors"] = Provenance.DATAFILE.value

    drop_orphaned_datafile_fields(book, Evidence(datafile=None))

    assert book.title == ""
    assert book.authors == []
    assert "title" not in book.provenance
    assert "authors" not in book.provenance


def test_drop_is_noop_when_datafile_present(tmp_path):
    from colophon.services.identify import drop_orphaned_datafile_fields

    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.title = "Keep Me"
    book.provenance["title"] = Provenance.DATAFILE.value

    drop_orphaned_datafile_fields(book, Evidence(datafile=DatafileSidecar()))

    assert book.title == "Keep Me"
    assert book.provenance["title"] == Provenance.DATAFILE.value


def test_drop_leaves_non_datafile_provenance(tmp_path):
    from colophon.services.identify import drop_orphaned_datafile_fields

    book = BookUnit.new(source_folder=tmp_path / "Book")
    book.title = "Manual Title"
    book.provenance["title"] = Provenance.MANUAL.value
    book.authors = ["From Audnexus"]
    book.provenance["authors"] = "audnexus"

    drop_orphaned_datafile_fields(book, Evidence(datafile=None))

    assert book.title == "Manual Title"
    assert book.provenance["title"] == Provenance.MANUAL.value
    assert book.authors == ["From Audnexus"]
    assert book.provenance["authors"] == "audnexus"


def test_run_identify_rederive_refills_title_from_tag(tmp_path):
    from colophon.services.identify import run_identify

    folder = tmp_path / "Some Author" / "Some Book"
    folder.mkdir(parents=True)
    f = folder / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TIT2(encoding=3, text=["Real Title"]))
    id3.save(f)

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(f)]
    book.title = "Stale Datafile Title"
    book.provenance["title"] = Provenance.DATAFILE.value

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.title == "Real Title"
    assert book.provenance["title"] == Provenance.TAG.value


def test_run_identify_titles_multifile_book_from_album_not_chapter(tmp_path):
    # A single book split into chapter files: each file's Title tag is a chapter, the shared Album is
    # the book. The book must be titled from the Album, not the first file's chapter Title tag.
    from colophon.services.identify import run_identify

    folder = tmp_path / "Don Miguel Ruiz" / "The Fifth Agreement"
    folder.mkdir(parents=True)
    files = []
    for i, chap in enumerate(["Chap 01 - In the Beginning", "Chap 02 - Symbols", "Epilogue"], 1):
        f = folder / f"The Fifth Agreement - {i:02d} - {chap}.mp3"
        f.write_bytes(b"")
        id3 = ID3()
        id3.add(TIT2(encoding=3, text=[chap]))
        id3.add(TALB(encoding=3, text=["The Fifth Agreement"]))
        id3.save(f)
        files.append(probe_audio_file(f))

    book = BookUnit.new(source_folder=folder)
    book.source_files = files
    book.content_kind = ContentKind.SINGLE
    book.detected_works = [DetectedWork(
        label="The Fifth Agreement", label_prov=Provenance.TAG.value,
        files=[f.path for f in files])]

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.title == "The Fifth Agreement"


def test_run_identify_rederive_clears_field_with_no_lower_tier(tmp_path):
    from colophon.services.identify import run_identify

    folder = tmp_path / "Some Author" / "Some Book"
    folder.mkdir(parents=True)
    f = folder / "01.mp3"
    f.write_bytes(b"")  # no embedded tags -> no lower tier for description

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(f)]
    book.description = "Stale datafile blurb"
    book.provenance["description"] = Provenance.DATAFILE.value

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.description == ""
    assert "description" not in book.provenance


def test_run_identify_rederive_clears_when_datafile_vetted_as_container(tmp_path):
    from colophon.services.identify import run_identify

    folder = tmp_path / "TE_Audiobooks_S" / "Sarah Graves"
    folder.mkdir(parents=True)
    (folder / "a.mp3").write_bytes(b"")
    (folder / "metadata.json").write_text(
        json.dumps({"title": "Sarah Graves", "authors": ["TE_Audiobooks_S"]}))

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(folder / "a.mp3")]
    book.content_kind = ContentKind.MULTI
    book.publisher = "Stale Publisher"
    book.provenance["publisher"] = Provenance.DATAFILE.value

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.publisher == ""
    assert "publisher" not in book.provenance


def test_run_identify_clears_orphaned_datafile_on_any_scan(tmp_path):
    # The datafile sidecar being gone is the trigger, not the scan mode: an orphaned DATAFILE field is
    # dropped on every scan (not just Refresh), so a plain rescan heals stale datafile sidecar data.
    from colophon.services.identify import run_identify

    folder = tmp_path / "Some Author" / "Some Book"
    folder.mkdir(parents=True)
    f = folder / "01.mp3"
    f.write_bytes(b"")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(f)]
    book.description = "Stale datafile blurb"
    book.provenance["description"] = Provenance.DATAFILE.value

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.description == ""
    assert "description" not in book.provenance


def _dir_titled(title: str) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/lib/x"))
    b.title = title
    b.provenance["title"] = Provenance.DIRECTORY.value
    return b


def test_normalize_strips_strong_leading_sequence():
    b = _dir_titled("05 - Phoenix")
    normalize(b)
    assert b.title == "Phoenix"


def test_normalize_leaves_weak_compound_title():
    b = _dir_titled("30-Day Heart Tune-Up")
    normalize(b)
    assert b.title == "30-Day Heart Tune-Up"        # weak affix: not stripped here


def test_normalize_leaves_tag_title_untouched():
    b = _dir_titled("02 - Yendi")
    b.provenance["title"] = Provenance.TAG.value
    normalize(b)
    assert b.title == "02 - Yendi"                   # only directory/filename titles are cleaned


def test_normalize_deshouts_shouting_tag_title():
    b = _dir_titled("DARKSABER")
    b.provenance["title"] = Provenance.TAG.value
    normalize(b)
    assert b.title == "Darksaber"                    # a shouting single-book tag title is de-shouted


def test_normalize_leaves_manual_shouting_title_verbatim():
    b = _dir_titled("DARKSABER")
    b.provenance["title"] = Provenance.MANUAL.value
    normalize(b)
    assert b.title == "DARKSABER"                    # the user typed it deliberately, kept


def test_normalize_proper_cases_shouting_directory_author():
    b = BookUnit.new(source_folder=Path("/lib/SANDRA BROWN"))
    b.authors = ["SANDRA BROWN"]
    b.provenance["authors"] = Provenance.DIRECTORY.value
    normalize(b)
    assert b.authors == ["Sandra Brown"]


def test_normalize_keeps_single_token_acronym_author():
    b = BookUnit.new(source_folder=Path("/lib/x"))
    b.authors = ["BBC"]
    b.provenance["authors"] = Provenance.TAG.value
    normalize(b)
    assert b.authors == ["BBC"]          # a single all-caps token is an initialism, kept


def test_normalize_deshouts_shouting_multiword_tag_author():
    b = BookUnit.new(source_folder=Path("/lib/x"))
    b.authors = ["TIMOTHY ZAHN"]
    b.provenance["authors"] = Provenance.TAG.value
    normalize(b)
    assert b.authors == ["Timothy Zahn"]  # a multi-word shouting name is de-shouted even from a tag


def test_normalize_leaves_manual_author_verbatim():
    b = BookUnit.new(source_folder=Path("/lib/x"))
    b.authors = ["TIMOTHY ZAHN"]
    b.provenance["authors"] = Provenance.MANUAL.value
    normalize(b)
    assert b.authors == ["TIMOTHY ZAHN"]  # the user typed it deliberately, kept


def test_title_folder_keeps_folder_name_over_filename_residue(tmp_path):
    # Folder "Cujo" is classified TITLE; its glued file "01Cujo.mp3" produces a DetectedWork
    # whose label "01Cujo" doesn't share a token with the folder name, so the old promotion
    # block would misfire and overwrite book.title with the residue.
    folder = tmp_path / "Cujo"
    book = BookUnit.new(source_folder=folder)
    book.content_kind = ContentKind.SINGLE
    book.folder_kind = FolderKind.TITLE
    book.title = "Cujo"                     # already set from the parsed folder name
    book.detected_works = [DetectedWork(label="01Cujo", files=[folder / "01Cujo.mp3"])]

    attribute(book, Evidence(first_path=folder / "01Cujo.mp3", embedded=None,
                             filename_fields={}, directory_fields={}))

    assert book.title == "Cujo"            # not promoted to the "01Cujo" residue


def test_untagged_folder_title_year_and_narrator_from_folder_name(tmp_path):
    from colophon.core.dirinfer import parse_scheme
    from colophon.core.filename_parser import compile_template
    from colophon.core.models import BookUnit, ContentKind
    from colophon.services.identify import run_identify

    folder = tmp_path / "Author" / "1981 - Cujo (read by Lorna Raver)"
    folder.mkdir(parents=True)
    for i in (1, 2, 3):
        (folder / f"0{i}Cujo.mp3").write_bytes(b"")

    book = BookUnit.new(source_folder=folder)
    book.source_files = [probe_audio_file(folder / f"0{i}Cujo.mp3") for i in (1, 2, 3)]
    book.content_kind = ContentKind.SINGLE

    run_identify(book, root=tmp_path, pattern=compile_template("$Author - $Title"),
                 scheme=parse_scheme(""))

    assert book.title == "Cujo"
    assert book.publish_year == 1981
    assert book.narrators == ["Lorna Raver"]
