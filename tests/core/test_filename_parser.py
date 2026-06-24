import pytest

from colophon.core.filename_parser import compile_template, parse_filename


def test_parses_author_title_template():
    pattern = compile_template("$Author - $Title")
    assert parse_filename(pattern, "Brandon Sanderson - The Way of Kings.mp3") == {
        "author": "Brandon Sanderson",
        "title": "The Way of Kings",
    }


def test_sernum_and_pubyear_map_to_model_fields():
    pattern = compile_template("$Series #$SerNum - $Title ($PubYear)")
    assert parse_filename(pattern, "Stormlight #1 - The Way of Kings (2010).mp3") == {
        "series": "Stormlight", "sequence": "1", "title": "The Way of Kings", "year": "2010",
    }


def test_skip_token_is_discarded():
    pattern = compile_template("$Skip - $Title")
    assert parse_filename(pattern, "01 - Dune.mp3") == {"title": "Dune"}


def test_trailing_skip_drops_junk_group():
    pattern = compile_template("$Author - $Title $Skip")
    assert parse_filename(pattern, "Herbert - Dune [Unabridged].mp3") == {
        "author": "Herbert", "title": "Dune",
    }


def test_whitespace_is_lenient():
    pattern = compile_template("$Author - $Title")
    assert parse_filename(pattern, "Herbert  -   Dune.mp3") == {
        "author": "Herbert", "title": "Dune",
    }


def test_double_dollar_is_literal():
    pattern = compile_template("$$ $Title")
    assert parse_filename(pattern, "$ Dune.mp3") == {"title": "Dune"}


def test_non_matching_filename_returns_none():
    pattern = compile_template("$Author - $Title")
    assert parse_filename(pattern, "no-delimiter-here.mp3") is None


def test_unknown_or_build_only_token_raises():
    with pytest.raises(ValueError, match="Unknown or non-parseable"):
        compile_template("$Bogus")
    with pytest.raises(ValueError, match="Unknown or non-parseable"):
        compile_template("$SortAuthor")  # build-only token is not parseable


def test_duplicate_field_raises():
    with pytest.raises(ValueError, match="more than once"):
        compile_template("$Title $Title")
