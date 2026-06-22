from colophon.core.catalog import (
    CATALOG_KINDS,
    CatalogEntry,
    entry_names,
    list_entries,
    remap_names,
)
from colophon.core.models import BookUnit, SeriesRef


def _book(tmp_path, **kw):
    b = BookUnit.new(source_folder=tmp_path / "x")
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_kinds():
    assert CATALOG_KINDS == ("author", "narrator", "series", "genre", "tag", "publisher", "language")


def test_entry_names_per_kind(tmp_path):
    b = _book(tmp_path, authors=["A", "B"], genres=["Sci-Fi", "Fantasy"],
              series=[SeriesRef(name="Dune", sequence=1.0)])
    assert entry_names(b, "author") == ["A", "B"]
    assert entry_names(b, "genre") == ["Sci-Fi", "Fantasy"]
    assert entry_names(b, "series") == ["Dune"]


def test_list_entries_counts(tmp_path):
    b1 = _book(tmp_path, genres=["Sci-Fi", "Fantasy"])
    b2 = _book(tmp_path, genres=["Sci-Fi"])
    entries = list_entries([b1, b2], "genre")
    assert CatalogEntry(name="Sci-Fi", count=2) in entries
    assert CatalogEntry(name="Fantasy", count=1) in entries


def test_remap_rename_dedupes():
    assert remap_names(["Sci-Fi", "Fantasy"], {"Sci-Fi": "Fantasy"}) == ["Fantasy"]


def test_remap_merge_and_delete():
    assert remap_names(["a", "b", "c"], {"a": "X", "b": "X"}) == ["X", "c"]
    assert remap_names(["a", "b"], {"a": None}) == ["b"]


def test_catalog_kinds_include_publisher_and_language():
    assert "publisher" in CATALOG_KINDS
    assert "language" in CATALOG_KINDS


def test_list_entries_publisher(tmp_path):
    b1 = _book(tmp_path, publisher="Tor")
    b2 = _book(tmp_path, publisher="Tor")
    b3 = _book(tmp_path, publisher="Macmillan")
    entries = {e.name: e.count for e in list_entries([b1, b2, b3], "publisher")}
    assert entries == {"Tor": 2, "Macmillan": 1}
