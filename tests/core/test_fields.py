from pathlib import Path

import pytest

from colophon.core.fields import EDITABLE_FIELDS, field_provenance, get_field, set_field
from colophon.core.models import BookUnit


def _book() -> BookUnit:
    return BookUnit.new(source_folder=Path("/ingest/x"))


def test_editable_fields_list():
    assert EDITABLE_FIELDS == [
        "title", "subtitle", "author", "narrator", "series",
        "sequence", "year", "asin", "language", "publisher", "description",
        "genre", "tag",
    ]


def test_scalar_get_set():
    b = _book()
    set_field(b, "title", "Dune")
    assert get_field(b, "title") == "Dune"
    assert b.title == "Dune"


def test_author_list_round_trips_via_semicolon():
    b = _book()
    set_field(b, "author", "Frank Herbert; Kevin J. Anderson")
    assert b.authors == ["Frank Herbert", "Kevin J. Anderson"]
    assert get_field(b, "author") == "Frank Herbert; Kevin J. Anderson"


def test_empty_value_clears_list_field():
    b = _book()
    b.authors = ["Someone"]
    set_field(b, "author", "")
    assert b.authors == []
    assert get_field(b, "author") is None


def test_series_and_sequence_address_first_series_ref():
    b = _book()
    set_field(b, "series", "Stormlight Archive")
    set_field(b, "sequence", "1")
    assert b.series[0].name == "Stormlight Archive"
    assert b.series[0].sequence == 1.0
    assert get_field(b, "series") == "Stormlight Archive"
    assert get_field(b, "sequence") == "1.0"


def test_year_coerces_int():
    b = _book()
    set_field(b, "year", "2021")
    assert b.publish_year == 2021
    assert get_field(b, "year") == "2021"


def test_unknown_field_raises():
    with pytest.raises(ValueError, match="unknown editable field"):
        get_field(_book(), "bogus")


def test_field_provenance_maps_editable_key_to_source():
    b = _book()
    b.authors = ["A"]
    b.provenance = {"authors": "audnexus", "title": "tag"}
    assert field_provenance(b, "author") == "audnexus"  # editable "author" -> stored "authors"
    assert field_provenance(b, "title") == "tag"


def test_field_provenance_none_when_unset():
    b = _book()
    assert field_provenance(b, "year") is None


def test_get_set_genre_round_trip():
    from pathlib import Path

    from colophon.core.fields import get_field, set_field
    from colophon.core.models import BookUnit
    b = BookUnit.new(source_folder=Path("/x"))
    set_field(b, "genre", "Fantasy; Epic")
    assert b.genres == ["Fantasy", "Epic"]
    assert get_field(b, "genre") == "Fantasy; Epic"
    set_field(b, "genre", None)
    assert b.genres == []
    assert get_field(b, "genre") is None


def test_get_set_tag_round_trip():
    from pathlib import Path

    from colophon.core.fields import get_field, set_field
    from colophon.core.models import BookUnit
    b = BookUnit.new(source_folder=Path("/x"))
    set_field(b, "tag", "to-relisten; gift")
    assert b.tags == ["to-relisten", "gift"]
    assert get_field(b, "tag") == "to-relisten; gift"


def test_genre_tag_provenance_keys():
    from colophon.core.fields import EDITABLE_TO_PROVENANCE
    assert EDITABLE_TO_PROVENANCE["genre"] == "genres"
    assert EDITABLE_TO_PROVENANCE["tag"] == "tags"
