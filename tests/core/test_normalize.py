from colophon.core.normalize import normalize_text


def test_titlecase_lowercases_small_words_but_not_first_or_last():
    assert normalize_text("the lord of the rings") == "The Lord of the Rings"
    assert normalize_text("a tale of two cities") == "A Tale of Two Cities"


def test_titlecase_uppercases_all_caps_input():
    assert normalize_text("THE WAY OF KINGS") == "The Way of Kings"


def test_first_word_after_colon_is_capitalized():
    assert normalize_text("mistborn: the final empire") == "Mistborn: The Final Empire"


def test_underscores_and_kebab_become_spaces():
    assert normalize_text("the_way_of_kings") == "The Way of Kings"
    assert normalize_text("the-way-of-kings") == "The Way of Kings"


def test_spaced_hyphen_is_kept_as_dash_with_single_spaces():
    assert normalize_text("Mistborn  -The Final Empire") == "Mistborn - The Final Empire"
    assert normalize_text("Mistborn-  The Final Empire") == "Mistborn - The Final Empire"


def test_comma_spacing():
    assert normalize_text("Kramer ,Reading") == "Kramer, Reading"
    assert normalize_text("Kramer,Reading") == "Kramer, Reading"
    assert normalize_text("Kramer,   Reading") == "Kramer, Reading"


def test_collapses_whitespace_and_trims():
    assert normalize_text("  the   way  ") == "The Way"


def test_empty_stays_empty():
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""
