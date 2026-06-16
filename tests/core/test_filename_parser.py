import pytest

from colophon.core.filename_parser import VALID_FILENAME_FIELDS, compile_template, parse_filename


def test_valid_fields_are_colophon_vocabulary():
    assert VALID_FILENAME_FIELDS == {
        "author", "narrator", "title", "subtitle", "series", "sequence", "year",
    }


def test_parses_author_title_template():
    pattern = compile_template("%author% - %title%")
    assert parse_filename(pattern, "Brandon Sanderson - The Way of Kings.mp3") == {
        "author": "Brandon Sanderson",
        "title": "The Way of Kings",
    }


def test_skip_placeholder_is_discarded():
    pattern = compile_template("%skip% - %title%")
    assert parse_filename(pattern, "01 - Dune.mp3") == {"title": "Dune"}


def test_non_matching_filename_returns_none():
    pattern = compile_template("%author% - %title%")
    assert parse_filename(pattern, "no-delimiter-here.mp3") is None


def test_unknown_placeholder_raises():
    with pytest.raises(ValueError, match="Unknown placeholder"):
        compile_template("%bogus%")


def test_duplicate_placeholder_raises():
    with pytest.raises(ValueError, match="more than once"):
        compile_template("%title% %title%")
