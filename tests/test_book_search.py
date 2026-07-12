from pathlib import Path

from colophon.core.book_search import (
    Condition,
    book_matches,
    format_query,
    format_token,
    parse_query,
)
from colophon.core.models import BookUnit, SeriesRef


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
    assert parse_query("sanderson kramer") == [
        Condition(None, "sanderson"),
        Condition(None, "kramer"),
    ]


def test_parse_field_condition_lowercases_value():
    assert parse_query("author:Sanderson") == [Condition("author", "sanderson")]


def test_parse_quoted_value_keeps_spaces():
    assert parse_query('title:"Way of Kings"') == [Condition("title", "way of kings")]


def test_parse_mixes_fields_and_bare_terms():
    assert parse_query('author:sanderson "kate reading" epic') == [
        Condition("author", "sanderson"),
        Condition(None, "kate reading"),
        Condition(None, "epic"),
    ]


def test_parse_unknown_prefix_is_bare_term():
    # colons that are not a known field must not be swallowed
    assert parse_query("http://example.com") == [Condition(None, "http://example.com")]


def test_parse_any_prefix_maps_to_any_field():
    assert parse_query("any:kramer") == [Condition(None, "kramer")]


def test_parse_empty_value_field_is_ignored_as_condition():
    # `author:` with no value contributes nothing
    assert parse_query("author:") == []


def test_parse_unbalanced_quote_falls_back_to_split():
    # must not raise; degrade to whitespace split
    assert parse_query('title:"way of') == [
        Condition("title", '"way'),
        Condition(None, "of"),
    ]


def test_parse_repeated_field_kept():
    assert parse_query("author:a author:b") == [
        Condition("author", "a"),
        Condition("author", "b"),
    ]


# --- formatting ---

def test_format_token_quotes_on_whitespace():
    assert format_token("title", "way of kings") == 'title:"way of kings"'
    assert format_token("author", "sanderson") == "author:sanderson"


def test_format_query_round_trips_through_parse():
    text = 'author:sanderson title:"way of kings" epic'
    assert parse_query(format_query(parse_query(text))) == parse_query(text)


# --- matching ---

def _matches(book, text):
    conds = parse_query(text)
    return book_matches(
        book,
        conds,
        filename="way-of-kings.m4b",
        any_haystack=f"{book.title} {'; '.join(book.authors)}".lower(),
    )


def test_match_field_substring():
    assert _matches(_book(), "author:sander")
    assert not _matches(_book(), "author:tolkien")


def test_match_multi_value_any_element():
    # narrator matches the second narrator
    assert _matches(_book(), "narrator:reading")


def test_match_series_by_name():
    assert _matches(_book(), 'series:stormlight')


def test_match_year_is_exact():
    assert _matches(_book(), "year:2014")
    assert not _matches(_book(), "year:201")  # substring must not match


def test_match_and_across_conditions():
    assert _matches(_book(), "author:sanderson narrator:kramer")
    assert not _matches(_book(), "author:sanderson narrator:nobody")


def test_match_filename_field():
    assert _matches(_book(), "filename:way-of-kings")


def test_match_bare_term_uses_any_haystack():
    assert _matches(_book(), "sanderson")
    assert not _matches(_book(), "nonexistentword")


def test_empty_query_matches_everything():
    assert book_matches(_book(), [], filename="x", any_haystack="x")
