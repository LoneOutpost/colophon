from __future__ import annotations

from colophon.core.metadata_quality import author_junk, title_junk


class TestAuthorJunk:
    def test_separator_span_is_junk(self):
        # the Gibran class: a whole "Author.-.Title" string smuggled as author
        assert author_junk("Kahlil Gibran.-.The Prophet") == 1.0

    def test_embedded_enumeration_pair_is_junk(self):
        assert author_junk("1 of 8 Diana Gabaldon") >= 0.9

    def test_bare_parenthetical_series_number_is_junk(self):
        assert author_junk("(5)") == 1.0                 # is_structural_marker / bare index

    def test_a_clean_author_is_not_junk(self):
        assert author_junk("Diana Gabaldon") == 0.0
        assert author_junk("Kahlil Gibran") == 0.0

    def test_nickname_parenthetical_is_not_junk(self):
        # census false positive: the parens are a nickname, not a series number
        assert author_junk("E. E. (Doc) Smith") == 0.0

    def test_a_lone_number_is_not_junk(self):
        # "Top 100" / a year: the pair is the signal, not the digit
        assert author_junk("Top 100 Sci-Fi Books") == 0.0


class TestTitleJunk:
    def test_bare_parenthesized_number_is_junk(self):
        assert title_junk("(5)") == 1.0

    def test_leading_index_prefix_is_junk(self):
        assert title_junk("07-Foundation and Earth") >= 0.7
        assert title_junk("18 - Childhood's End") >= 0.7

    def test_embedded_of_pair_is_junk(self):
        assert title_junk("01 of 01 Murder on the Orient Express") >= 0.8
        assert title_junk("Isaac Asimov - Nemesis - 01 of 09") >= 0.8

    def test_four_digit_led_title_is_not_junk(self):
        # census false positive: the year IS the title, not an index
        assert title_junk("2001 - A Space Odyssey") == 0.0
        assert title_junk("3001 The Final Odyssey") == 0.0

    def test_a_clean_title_is_not_junk(self):
        assert title_junk("Fiery Cross") == 0.0
        assert title_junk("The Prophet") == 0.0
