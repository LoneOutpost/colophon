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


def test_series_name_with_internal_digit_not_split_into_sequence():
    # a series name containing a digit ("F.B.I. K-9") must not have that digit stolen by
    # the sequence group: $SerNum is numeric, so only the real trailing number is the sequence
    pattern = compile_template("$Title ($Series $SerNum)")
    assert parse_filename(pattern, "Cold Pursuit (F.B.I. K-9 3).mp3") == {
        "title": "Cold Pursuit", "series": "F.B.I. K-9", "sequence": "3",
    }
    assert parse_filename(pattern, "Scent of Fear (F.B.I. K-9 12).mp3") == {
        "title": "Scent of Fear", "series": "F.B.I. K-9", "sequence": "12",
    }


def test_decimal_sequence_is_parsed():
    pattern = compile_template("$Title ($Series $SerNum)")
    assert parse_filename(pattern, "Novella (Some Series 2.5).mp3") == {
        "title": "Novella", "series": "Some Series", "sequence": "2.5",
    }


def test_sequence_group_requires_a_number():
    # with no trailing number the sequence cannot be satisfied, so the template does not match
    # (better than capturing non-numeric junk as the sequence)
    pattern = compile_template("$Title ($Series $SerNum)")
    assert parse_filename(pattern, "Cold Pursuit (F.B.I. K-9).mp3") is None


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


def test_conditional_group_bracket_rejected_in_parse_pattern():
    # [ ... ] conditional groups are a build-only feature; parse patterns must reject them.
    with pytest.raises(ValueError, match="organize"):
        compile_template("[$SerNum - ]$Title")
    with pytest.raises(ValueError, match="organize"):
        compile_template("$Title]")


def test_underscore_directly_after_token_is_a_literal_separator():
    # '$Skip_$Author' must read as $Skip, literal '_', $Author — not a 'Skip_' token.
    pattern = compile_template("$Skip_$Author")
    assert parse_filename(pattern, "01_STEPHEN_KING.mp3") == {"author": "STEPHEN KING"}


def test_uppercase_tokens_resolve():
    # token names are case-insensitive, so a caps template works
    pattern = compile_template("$SKIP_$AUTHOR")
    assert parse_filename(pattern, "01_STEPHEN_KING.mp3") == {"author": "STEPHEN KING"}


def test_underscores_between_two_fields_read_as_spaces():
    pattern = compile_template("$Author_-_$Title")
    assert parse_filename(pattern, "STEPHEN_KING_-_THE_SHINING.mp3") == {
        "author": "STEPHEN KING", "title": "THE SHINING",
    }


def test_underscore_in_value_becomes_space_for_any_separator():
    pattern = compile_template("$Author - $Title")
    assert parse_filename(pattern, "Brandon_Sanderson - The_Way_of_Kings.mp3") == {
        "author": "Brandon Sanderson", "title": "The Way of Kings",
    }


def test_repeated_underscores_collapse_to_one_space():
    pattern = compile_template("$Author")
    assert parse_filename(pattern, "Stephen__King.mp3") == {"author": "Stephen King"}


def test_numeric_tokens_and_double_dollar_unaffected_by_underscore_cleanup():
    pattern = compile_template("$Series #$SerNum ($PubYear)")
    assert parse_filename(pattern, "Stormlight #1 (2010).mp3") == {
        "series": "Stormlight", "sequence": "1", "year": "2010",
    }
    literal = compile_template("$$_$Title")  # underscore literal after a $$ literal
    assert parse_filename(literal, "$_Dune.mp3") == {"title": "Dune"}
