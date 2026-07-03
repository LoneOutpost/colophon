from colophon.core.match import clean_match_title, ratio, title_author_score


def test_ratio_is_case_and_space_insensitive():
    assert ratio("The Way of Kings", "the way of kings") == 1.0
    assert ratio("Dune", "Dune ") == 1.0


def test_ratio_partial():
    assert 0.0 < ratio("Dune", "Dune Messiah") < 1.0


def test_ratio_empty_inputs_are_zero():
    assert ratio("", "x") == 0.0
    assert ratio(None, "x") == 0.0


def test_title_author_score_combines_both():
    perfect = title_author_score("Dune", ["Frank Herbert"], "Dune", ["Frank Herbert"])
    assert perfect == 1.0
    title_only = title_author_score("Dune", ["Frank Herbert"], "Dune", ["Someone Else"])
    assert 0.0 < title_only < 1.0


def test_ratio_token_aware_handles_word_reordering():
    # char-sequence ratio is low for reordered words; token overlap rescues it
    assert ratio("Sanderson Brandon", "Brandon Sanderson") >= 0.9


def test_clean_match_title_strips_year_prefix_and_edition_paren():
    assert clean_match_title("1982 - The Gunslinger (DT1 - original edition)") == "The Gunslinger"


def test_clean_match_title_keeps_numeric_title_before_colon():
    # A leading number before a colon is subtitle punctuation, not a year prefix, so it stays.
    assert clean_match_title("2001: A Space Odyssey") == "2001: A Space Odyssey"


def test_clean_match_title_strip_year_false_keeps_year_prefix():
    # The stored-title path (strip_year=False) keeps a leading year but still cleans format cruft.
    assert clean_match_title("1982 - The Gunslinger (Unabridged)", strip_year=False) == "1982 - The Gunslinger"


def test_clean_match_title_keeps_series_paren():
    assert clean_match_title("The Gunslinger (The Dark Tower #1)") == "The Gunslinger (The Dark Tower #1)"


def test_clean_match_title_leaves_clean_title_untouched():
    assert clean_match_title("Elantris") == "Elantris"


def test_clean_match_title_strips_unabridged_paren():
    assert clean_match_title("Mistborn (Unabridged)") == "Mistborn"


def test_clean_match_title_strips_trailing_format_word():
    assert clean_match_title("The Way of Kings - Unabridged") == "The Way of Kings"


def test_clean_match_title_none_is_empty():
    assert clean_match_title(None) == ""


def test_clean_match_title_falls_back_when_cleaning_empties():
    assert clean_match_title("(Unabridged)") == "(Unabridged)"
