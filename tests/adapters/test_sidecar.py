import json
from pathlib import Path

from colophon.adapters.sidecar import SidecarMetadata, read_sidecar, write_sidecar
from colophon.core.models import BookUnit, SeriesRef


def _write(folder: Path, data: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(json.dumps(data))
    return folder


def test_absent_sidecar_returns_none(tmp_path):
    assert read_sidecar(tmp_path) is None


def test_reads_abs_style_sidecar(tmp_path):
    folder = _write(tmp_path / "book", {
        "title": "Dirk Gently's Holistic Detective Agency",
        "subtitle": None,
        "authors": ["Douglas Adams"],
        "narrators": ["Douglas Adams"],
        "series": ["Dirk Gently #1"],
        "publishedYear": "2010",
        "publisher": "Del Rey",
        "description": "A holistic detective.",
        "asin": "B0041G6CSI",
    })
    sc = read_sidecar(folder)
    assert isinstance(sc, SidecarMetadata)
    assert sc.title == "Dirk Gently's Holistic Detective Agency"
    assert sc.authors == ["Douglas Adams"]
    assert sc.narrators == ["Douglas Adams"]
    assert sc.series_name == "Dirk Gently"
    assert sc.series_sequence == 1.0
    assert sc.publish_year == 2010
    assert sc.publisher == "Del Rey"
    assert sc.asin == "B0041G6CSI"


def test_series_without_number_keeps_name_only(tmp_path):
    folder = _write(tmp_path / "b", {"title": "T", "series": ["Standalone"]})
    sc = read_sidecar(folder)
    assert sc.series_name == "Standalone"
    assert sc.series_sequence is None


def test_malformed_json_returns_none(tmp_path):
    (tmp_path / "metadata.json").write_text("{ not json")
    assert read_sidecar(tmp_path) is None


def test_empty_optional_fields_are_none(tmp_path):
    folder = _write(tmp_path / "b", {"title": "T", "authors": [], "series": []})
    sc = read_sidecar(folder)
    assert sc.title == "T"
    assert sc.authors == []
    assert sc.series_name is None
    assert sc.publish_year is None


def test_non_string_scalar_fields_do_not_crash(tmp_path):
    folder = _write(tmp_path / "b", {"title": 123, "asin": 999, "authors": ["Real Author"]})
    sc = read_sidecar(folder)
    assert isinstance(sc, SidecarMetadata)
    assert sc.title is None
    assert sc.asin is None
    assert sc.authors == ["Real Author"]


def test_integer_published_year_is_handled(tmp_path):
    folder = _write(tmp_path / "b", {"title": "T", "publishedYear": 1999})
    sc = read_sidecar(folder)
    assert sc.publish_year == 1999


def _book(folder: Path) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.title = "Dirk Gently"
    b.authors = ["Douglas Adams"]
    b.narrators = ["Douglas Adams"]
    b.series = [SeriesRef(name="Dirk Gently", sequence=1.0)]
    b.publish_year = 1987
    b.description = "A holistic detective."
    b.asin = "B0041G6CSI"
    return b


def test_write_then_read_round_trips(tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    book = _book(folder)
    write_sidecar(folder, book)
    sc = read_sidecar(folder)
    assert sc.title == "Dirk Gently"
    assert sc.authors == ["Douglas Adams"]
    assert sc.series_name == "Dirk Gently"
    assert sc.series_sequence == 1.0
    assert sc.publish_year == 1987
    assert sc.asin == "B0041G6CSI"


def test_write_formats_whole_sequence_without_decimal(tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    book = _book(folder)
    write_sidecar(folder, book)
    raw = json.loads((folder / "metadata.json").read_text())
    assert raw["series"] == ["Dirk Gently #1"]  # not "#1.0"


def test_write_preserves_unmanaged_keys(tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "title": "old", "genres": ["Sci-Fi"], "chapters": [{"t": 0}], "customKey": 42,
    }))
    write_sidecar(folder, _book(folder))
    raw = json.loads((folder / "metadata.json").read_text())
    assert raw["title"] == "Dirk Gently"          # managed field updated
    assert raw["genres"] == ["Sci-Fi"]            # unmanaged field preserved
    assert raw["chapters"] == [{"t": 0}]
    assert raw["customKey"] == 42


def test_write_creates_file_when_absent(tmp_path):
    folder = tmp_path / "newbook"
    folder.mkdir()
    write_sidecar(folder, _book(folder))
    assert (folder / "metadata.json").exists()


def test_write_leaves_no_tmp_file(tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    write_sidecar(folder, _book(folder))
    leftovers = [p.name for p in folder.iterdir() if p.suffix == ".tmp" or p.name.endswith(".tmp")]
    assert leftovers == []


def test_write_over_corrupt_existing_sidecar(tmp_path):
    folder = tmp_path / "book"
    folder.mkdir()
    (folder / "metadata.json").write_text("{ not json")
    write_sidecar(folder, _book(folder))  # must not raise; corrupt file is overwritten
    assert read_sidecar(folder).title == "Dirk Gently"
