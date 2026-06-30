from pathlib import Path

from colophon.core.entity_alias import canonical_book, resolve_alias
from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookUnit, SeriesRef


def _book(*, authors=None, series=None) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/x"))
    b.title = "T"
    b.authors = authors or []
    b.series = series or []
    return b


def test_resolve_alias_still_works_from_new_module():
    aliases = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    assert resolve_alias(aliases, "author", "B. Sanderson") == "Brandon Sanderson"
    assert resolve_alias({}, "author", "B. Sanderson") == "B. Sanderson"


def test_canonical_book_passthrough_when_no_overrides():
    b = _book(authors=["B. Sanderson"])
    assert canonical_book(b, {}) is b


def test_canonical_book_remaps_author_name():
    b = _book(authors=["B. Sanderson"])
    overrides = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    out = canonical_book(b, overrides)
    assert out.authors == ["Brandon Sanderson"]
    assert b.authors == ["B. Sanderson"]


def test_canonical_book_remaps_series_name_preserving_sequence():
    b = _book(authors=["x"], series=[SeriesRef(name="Mistborn Era 1", sequence=2.0)])
    overrides = {("series", _name_key("Mistborn Era 1")): "Mistborn"}
    out = canonical_book(b, overrides)
    assert out.series[0].name == "Mistborn"
    assert out.series[0].sequence == 2.0
    assert b.series[0].name == "Mistborn Era 1"


def test_canonical_book_preserves_identity_fields():
    b = _book(authors=["B. Sanderson"])
    overrides = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    out = canonical_book(b, overrides)
    assert out.id == b.id
    assert out.source_folder == b.source_folder
    assert out.source_files == b.source_files
