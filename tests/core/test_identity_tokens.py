from __future__ import annotations

from colophon.core.identity_tokens import title_candidates


def test_drops_author_and_series_marker():
    name = "Alan Dean Foster.-.Flinx Bk03.-.Orphan Star"
    assert title_candidates(name, authors=["Alan Dean Foster"], series=[]) == ["Orphan Star"]


def test_author_match_folds_initials_via_normalize_key():
    name = "HP Lovecraft - The Fungi From Yuggoth"
    assert title_candidates(name, authors=["H. P. Lovecraft"], series=[]) == ["The Fungi From Yuggoth"]


def test_drops_bracketed_and_structural_tokens():
    assert title_candidates("Cujo (read by Lorna Raver)", authors=[], series=[]) == ["Cujo"]
    assert title_candidates("Title - 01 of 12", authors=[], series=[]) == ["Title"]


def test_bare_number_title_survives():
    assert title_candidates("1984", authors=[], series=[]) == ["1984"]


def test_author_only_folder_yields_no_title():
    assert title_candidates("Alexei Panshin", authors=["Alexei Panshin"], series=[]) == []


import pytest
from colophon.core.identity_tokens import _clean_token, title_candidates


@pytest.mark.parametrize("junk", ["1/9", "01-12", "001 of 153", "CD01", "cd1", "1 of 8", "01 of 6"])
def test_clean_token_drops_a_whole_index_or_disc_marker(junk):
    assert _clean_token(junk) is None


@pytest.mark.parametrize("dirty,clean", [
    ("Innocence In Death CD03-01", "Innocence In Death"),   # CD-track compound
    ("Red Storm Rising CD 01 of 40", "Red Storm Rising"),   # CD N of M
    ("BSLC01 China Trade CD1of8", "BSLC01 China Trade"),     # glued CDNofM (leading code kept)
    ("The Spiderwick Chronicles CD01", "The Spiderwick Chronicles"),
    ("01 The Coming of the Ship", "The Coming of the Ship"), # leading padded index
])
def test_clean_token_strips_index_disc_affixes(dirty, clean):
    assert _clean_token(dirty) == clean


@pytest.mark.parametrize("legit", ["1984", "Slaughterhouse 5", "Catch 22", "Fahrenheit 451", "Apollo 13", "The Hobbit"])
def test_clean_token_keeps_a_legit_title_incl_bare_number(legit):
    assert _clean_token(legit) == legit


def test_title_candidates_cleans_disc_and_index_tokens():
    # a filename with author paren + a CD-track title token -> just the clean title
    assert title_candidates("(JD Robb) Innocence In Death CD03-01", authors=["J. D. Robb"], series=[]) \
        == ["Innocence In Death"]


@pytest.mark.parametrize("junk", ["Unb", "UA 1@64.44m", "UA 1-64.44m"])
def test_clean_token_drops_a_bare_encoding_marker(junk):
    assert _clean_token(junk) is None


@pytest.mark.parametrize("dirty,clean", [
    ("The Hive UA 1@64.22m", "The Hive"),               # trailing UA@bitrate
    ("The Florians UA", "The Florians"),                 # trailing bare UA
    ("{Arcane Society #1} Second Sight", "Second Sight"),  # title after the ref close-brace
    ("Tobias March #1} Slightly Shady", "Slightly Shady"),
])
def test_clean_token_strips_encoding_and_curly_ref(dirty, clean):
    assert _clean_token(dirty) == clean


def test_clean_token_drops_a_lone_curly_ref_fragment():
    assert _clean_token("{Lavinia Lake") is None


def test_clean_token_keeps_a_word_that_merely_contains_ua():
    assert _clean_token("Ultramarine") == "Ultramarine"     # \bua\b does not match mid-word


def test_glued_bk_series_marker_is_dropped_but_textbook_survives():
    # 'P&FBk 12' is a glued series abbreviation -> dropped; 'Textbook 1' is a real word -> kept
    assert title_candidates("Trouble Magnet - P&FBk 12", authors=["Alan Dean Foster"], series=[]) \
        == ["Trouble Magnet"]
    assert "Textbook 1" in title_candidates("Textbook 1", authors=[], series=[])


def test_joined_author_form_is_dropped_but_ampersand_title_survives():
    a = ["Allan Cole", "Chris Bunch"]
    assert title_candidates("Allan Cole & Chris Bunch - The Far Kingdoms", authors=a, series=[]) \
        == ["The Far Kingdoms"]
    # a real title that merely contains '&' is not an author
    assert title_candidates("Intro & Dedication", authors=a, series=[]) == ["Intro & Dedication"]
