from pathlib import Path

import pytest

from colophon.core.fields import EDITABLE_FIELDS, get_field, set_field
from colophon.core.models import BookUnit


def _book() -> BookUnit:
    return BookUnit.new(source_folder=Path("/ingest/x"))


def test_editable_fields_list():
    assert EDITABLE_FIELDS == [
        "title", "subtitle", "author", "narrator", "series",
        "sequence", "year", "asin", "language", "publisher", "description",
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
