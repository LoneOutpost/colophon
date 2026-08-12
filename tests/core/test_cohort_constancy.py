from __future__ import annotations

from colophon.core.cohort_constancy import cohort_constant_tokens


def test_voyager_keeps_constant_author_series_title_drops_disc_index():
    names = [
        "Diana Gabaldon - (Outlander 3) Voyager - D01.01-23",
        "Diana Gabaldon - (Outlander 3) Voyager - D01.02-23",
    ]
    assert cohort_constant_tokens(names) == {"Diana Gabaldon", "(Outlander 3)", "Voyager"}


def test_dot_dash_dot_is_normalized_to_space_dash_space():
    names = [
        "Alan Dean Foster.-.Flinx Bk12 - 01-Trouble",
        "Alan Dean Foster.-.Flinx Bk12 - 02-More",
    ]
    assert cohort_constant_tokens(names) == {"Alan Dean Foster", "Flinx Bk12"}


def test_bracketed_span_is_its_own_token_with_brackets_retained():
    names = [
        "Author - [Pip & Flinx 12] - 01-A",
        "Author - [Pip & Flinx 12] - 02-B",
    ]
    assert cohort_constant_tokens(names) == {"Author", "[Pip & Flinx 12]"}


def test_parenthetical_narrator_is_isolated_from_title():
    names = [
        "A Dance With Dragons (read by Roy Dotrice) - 01",
        "A Dance With Dragons (read by Roy Dotrice) - 02",
    ]
    assert cohort_constant_tokens(names) == {"A Dance With Dragons", "(read by Roy Dotrice)"}


def test_residual_text_splits_around_a_span():
    names = ["Foo (Bar) Baz - 1", "Foo (Bar) Baz - 2"]
    assert cohort_constant_tokens(names) == {"Foo", "(Bar)", "Baz"}


def test_glued_index_degrades_to_the_clean_leading_token():
    names = ["Coyote - Unb-001", "Coyote - Unb-002"]
    assert cohort_constant_tokens(names) == {"Coyote"}


def test_all_distinct_titles_yield_empty_set():
    names = ["01 - Title", "02 - Prologue", "03 - Bran"]
    assert cohort_constant_tokens(names) == set()


def test_intersection_is_case_insensitive_and_keeps_first_display_casing():
    names = ["Author - Title", "author - Other"]
    assert cohort_constant_tokens(names) == {"Author"}


def test_single_file_cohort_returns_empty():
    assert cohort_constant_tokens(["Diana Gabaldon - Voyager - 01"]) == set()


def test_empty_list_returns_empty():
    assert cohort_constant_tokens([]) == set()
