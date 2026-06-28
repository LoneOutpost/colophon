import json

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
    assert book.title == "The Gunslinger"
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
