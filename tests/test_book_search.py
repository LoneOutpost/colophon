from pathlib import Path

from colophon.core.book_search import (
    Condition,
    book_matches,
    build_token,
    format_query,
    format_token,
    parse_query,
)
from colophon.core.models import BookUnit, SeriesRef


def C(field, *values, neg=False):
    """Terse Condition builder for assertions."""
    return Condition(field, tuple(values), neg)


def _book() -> BookUnit:
    b = BookUnit.new(source_folder=Path("/lib/Sanderson/Way of Kings"))
    b.title = "The Way of Kings"
    b.subtitle = "Book One"
    b.authors = ["Brandon Sanderson"]
    b.narrators = ["Michael Kramer", "Kate Reading"]
    b.series = [SeriesRef(name="The Stormlight Archive", sequence=1.0)]
    b.franchise = "Cosmere"
    b.publisher = "Macmillan Audio"
    b.genres = ["Epic Fantasy"]
    b.tags = ["to-relisten"]
    b.asin = "B00INS9WQ2"
    b.isbn = "9781427214157"
    b.publish_year = 2014
    b.language = "English"
    b.description = "The first book of the Stormlight Archive."
    return b


# --- parsing ---

def test_parse_bare_terms_are_any_field():
    assert parse_query("sanderson kramer") == [C(None, "sanderson"), C(None, "kramer")]


def test_parse_field_condition_lowercases_value():
    assert parse_query("author:Sanderson") == [C("author", "sanderson")]


def test_parse_quoted_value_keeps_spaces():
    assert parse_query('title:"Way of Kings"') == [C("title", "way of kings")]


def test_parse_mixes_fields_and_bare_terms():
    assert parse_query('author:sanderson "kate reading" epic') == [
        C("author", "sanderson"),
        C(None, "kate reading"),
        C(None, "epic"),
    ]


def test_parse_unknown_prefix_is_bare_term():
    assert parse_query("http://example.com") == [C(None, "http://example.com")]


def test_parse_any_prefix_maps_to_any_field():
    assert parse_query("any:kramer") == [C(None, "kramer")]


def test_parse_empty_value_field_is_ignored_as_condition():
    assert parse_query("author:") == []


def test_parse_repeated_field_kept():
    assert parse_query("author:a author:b") == [C("author", "a"), C("author", "b")]


# --- negation ---

def test_parse_negated_field():
    assert parse_query("-narrator:kramer") == [C("narrator", "kramer", neg=True)]


def test_parse_negated_bare_term():
    assert parse_query("-abridged") == [C(None, "abridged", neg=True)]


def test_parse_lone_dash_is_a_bare_term_not_negation():
    assert parse_query("-") == [C(None, "-")]


# --- within-field OR ---

def test_parse_comma_splits_or_alternatives():
    assert parse_query("author:sanderson,jordan") == [C("author", "sanderson", "jordan")]


def test_parse_quoted_comma_stays_literal():
    # the comma inside quotes is part of the value, not an OR separator
    assert parse_query('author:"herbert, frank"') == [C("author", "herbert, frank")]


def test_parse_mixed_quoted_and_or_alternative():
    assert parse_query('author:"le guin",tolkien') == [C("author", "le guin", "tolkien")]


def test_parse_negated_or_condition():
    assert parse_query("-author:sanderson,jordan") == [
        C("author", "sanderson", "jordan", neg=True)
    ]


# --- formatting ---

def test_format_token_quotes_on_whitespace_and_comma():
    assert format_token("title", ["way of kings"]) == 'title:"way of kings"'
    assert format_token("author", ["sanderson", "jordan"]) == "author:sanderson,jordan"
    assert format_token("narrator", ["kramer"], negated=True) == "-narrator:kramer"


def test_build_token_from_builder_input():
    assert build_token("author", "sanderson, jordan", negated=False) == "author:sanderson,jordan"
    assert build_token("narrator", "kramer", negated=True) == "-narrator:kramer"
    # any-field keeps the whole text as one literal bare term
    assert build_token("any", "kate reading", negated=False) == '"kate reading"'
    assert build_token("any", "", negated=True) == ""


def test_format_query_round_trips_through_parse():
    text = '-narrator:kramer author:sanderson,jordan title:"way of kings" epic'
    assert parse_query(format_query(parse_query(text))) == parse_query(text)


# --- matching ---

def _matches(book, text):
    conds = parse_query(text)
    return book_matches(
        book,
        conds,
        filename="way-of-kings.m4b",
        any_haystack=f"{book.title} {'; '.join(book.authors)} {'; '.join(book.narrators)}".lower(),
    )


def test_match_field_substring():
    assert _matches(_book(), "author:sander")
    assert not _matches(_book(), "author:tolkien")


def test_match_multi_value_any_element():
    assert _matches(_book(), "narrator:reading")


def test_match_year_is_exact():
    assert _matches(_book(), "year:2014")
    assert not _matches(_book(), "year:201")


def test_match_and_across_conditions():
    assert _matches(_book(), "author:sanderson narrator:kramer")
    assert not _matches(_book(), "author:sanderson narrator:nobody")


def test_match_negated_field_excludes():
    assert not _matches(_book(), "-narrator:kramer")
    assert _matches(_book(), "-narrator:nobody")


def test_match_negated_with_positive():
    # by Sanderson but not narrated by someone absent -> matches
    assert _matches(_book(), "author:sanderson -narrator:nobody")
    # by Sanderson but excluding Kramer -> excluded
    assert not _matches(_book(), "author:sanderson -narrator:kramer")


def test_match_or_alternatives():
    assert _matches(_book(), "author:tolkien,sanderson")  # second alt hits
    assert not _matches(_book(), "author:tolkien,jordan")  # neither hits


def test_match_negated_or_excludes_any_alternative():
    # exclude books whose narrator is Kramer OR Reading -> this book has both, excluded
    assert not _matches(_book(), "-narrator:kramer,reading")
    assert _matches(_book(), "-narrator:nobody,nowhere")


def test_match_year_or_alternatives_exact():
    assert _matches(_book(), "year:2013,2014")
    assert not _matches(_book(), "year:2012,2013")


def test_empty_query_matches_everything():
    assert book_matches(_book(), [], filename="x", any_haystack="x")
