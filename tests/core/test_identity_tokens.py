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

from colophon.core.identity_tokens import _clean_token


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


def test_title_candidates_strips_a_glued_part_index():
    # a per-file chapter part index glued to the title ('Dark Jenny-Part01') is stripped, so the
    # grouped files share the constant title 'Dark Jenny' for the cohort to recover.
    assert title_candidates("Dark Jenny-Part01", authors=[], series=[]) == ["Dark Jenny"]
    assert title_candidates("Dark Jenny - Part 12", authors=[], series=[]) == ["Dark Jenny"]
    # a real word 'Part' not followed by a number is untouched
    assert title_candidates("Part of the Pattern", authors=[], series=[]) == ["Part of the Pattern"]


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


def test_leaf_folder_author_extracts_first_dotdash_segment():
    from colophon.core.identity_tokens import leaf_folder_author
    assert leaf_folder_author("Kim Stanley Robinson.-.Galileos Dream.-.Unb") == "Kim Stanley Robinson"
    assert leaf_folder_author("Kahlil Gibran.-.The Prophet") == "Kahlil Gibran"
    assert leaf_folder_author("A. Lee Martinez.-.The Automatic Detective.-.UA  8@64.32m") == "A. Lee Martinez"


def test_leaf_folder_author_returns_none_without_convention_or_a_stopword_head():
    from colophon.core.identity_tokens import leaf_folder_author
    assert leaf_folder_author("Some Title Without Dotdash") is None
    assert leaf_folder_author("[Ciaphas Cain 13] Dead in the Water") is None
    # a staging/format word heading a mis-formed leaf is not the author
    assert leaf_folder_author("Audiobook.-.Isaac Asimov.-.Robot Visions") is None


def test_bare_name_number_series_prefix_dropped_when_a_title_follows():
    # 'Renegades of Pern 08 - The Skies of Pern' — the bare 'Name NN' prefix (no Bk/#) is the series.
    assert title_candidates("Renegades of Pern 08 - The Skies of Pern", authors=[], series=[]) \
        == ["The Skies of Pern"]
    assert title_candidates("Warlord 2 - Enemy of God", authors=[], series=[]) == ["Enemy of God"]
    # a '.-.' middle segment is the series when a title segment follows it
    assert title_candidates("J D Robb.-.Eve Dallas 11.-.Witness in Death",
                            authors=["J D Robb"], series=[]) == ["Witness in Death"]


def test_part_markers_never_leak_as_a_title_token():
    # a token that IS a part marker (incl. hyphen-glued/glued forms, or one that surfaces after a
    # lead/trail strip) is dropped from the title candidates, not kept as a title word.
    assert title_candidates("Eoin Colfer - And Another Thing - Pt1 Trk01",
                            authors=["Eoin Colfer"], series=[]) == ["And Another Thing"]
    assert title_candidates("The Switch - Ch01", authors=[], series=[]) == ["The Switch"]
    assert title_candidates("01. Track 1", authors=[], series=[]) == []
    # a real title with a trailing number is NOT a part marker and stays
    assert title_candidates("Slaughterhouse 5", authors=[], series=[]) == ["Slaughterhouse 5"]
    assert title_candidates("Apollo 13", authors=[], series=[]) == ["Apollo 13"]


def test_glued_series_code_segment_dropped_when_a_title_follows():
    # a glued caps-code + book number ('PM03', 'TR02', 'B&M03') is a series prefix, dropped when a
    # plainer title segment sits beside it — same rule as a bare 'Name NN'.
    assert title_candidates("Raymond Chandler.-.PM03.-.The High Window",
                            authors=["Raymond Chandler"], series=[]) == ["The High Window"]
    assert title_candidates("Gregg Hurwitz - TR02 - The Program",
                            authors=["Gregg Hurwitz"], series=[]) == ["The Program"]
    # an ordinary capitalized title word is never mistaken for a code
    assert title_candidates("2001 A Space Odyssey", authors=[], series=[]) == ["2001 A Space Odyssey"]


def test_lone_name_number_is_kept_as_the_title():
    # nothing plainer beside it -> a 'Name NN' IS the title, not a series ref.
    assert title_candidates("Slaughterhouse 5", authors=[], series=[]) == ["Slaughterhouse 5"]
    assert title_candidates("Apollo 13", authors=[], series=[]) == ["Apollo 13"]
    assert title_candidates("Fahrenheit 451", authors=[], series=[]) == ["Fahrenheit 451"]


def test_parse_series_ref():
    from colophon.core.identity_tokens import parse_series_ref
    assert parse_series_ref("Eve Dallas 11") == ("Eve Dallas", 11.0)
    assert parse_series_ref("Flinx Bk03") == ("Flinx", 3.0)
    assert parse_series_ref("Renegades of Pern 08") == ("Renegades of Pern", 8.0)
    assert parse_series_ref("Halfblood Chronicles Bk1") == ("Halfblood Chronicles", 1.0)  # marker consumed
    assert parse_series_ref("Arcane Society #2") == ("Arcane Society", 2.0)
    assert parse_series_ref("Witness in Death") is None   # no trailing number
    assert parse_series_ref("11") is None                 # no name (letters)
    # a structural marker / bare sequence word is never a series name
    assert parse_series_ref("Book 7") is None
    assert parse_series_ref("Bk3") is None
    assert parse_series_ref("CD02") is None
    assert parse_series_ref("Disc 2") is None
    # an 'N of M' part index is not a series ref
    assert parse_series_ref("Disc 08 of 11") is None
    assert parse_series_ref("Something 1 of 9") is None


def test_leaf_functions_are_delimiter_agnostic():
    # the `.-.` form is not special: a ` - ` or `_-_` delimited leaf parses identically.
    from colophon.core.identity_tokens import leaf_folder_author, leaf_folder_series
    assert leaf_folder_author("Don Pendleton - Joe Copp Bk01 - Copp for Hire - 1987") == "Don Pendleton"
    assert leaf_folder_author("Don Pendleton.-.Joe Copp Bk01.-.Copp for Hire.-.1987") == "Don Pendleton"
    assert leaf_folder_series("Don Pendleton - Joe Copp Bk01 - Copp for Hire - 1987") == ("Joe Copp", 1.0)
    assert leaf_folder_series("Barry Sadler - Casca 05 - Casca The Barbarian") == ("Casca", 5.0)
    # the junk guard reaches the leaf helper too: a disc/book marker middle is not a series
    assert leaf_folder_series("Narnia - Book 7 - The Last Battle 1") is None


def test_leaf_folder_series_reads_the_middle_dotdash_segment():
    from colophon.core.identity_tokens import leaf_folder_series
    assert leaf_folder_series("J D Robb.-.Eve Dallas 11.-.Witness in Death") == ("Eve Dallas", 11.0)
    assert leaf_folder_series("Alan Dean Foster.-.Flinx Bk03.-.Orphan Star") == ("Flinx", 3.0)
    # no middle segment / no series-shaped middle -> None
    assert leaf_folder_series("A E Van Vogt.-.Slan") is None
    assert leaf_folder_series("A. Lee Martinez.-.The Automatic Detective.-.UA  8@64.32m") is None
    assert leaf_folder_series("Kahlil Gibran.-.The Prophet") is None
