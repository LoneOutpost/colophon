from colophon.core.sequence_affix import parse_sequence_affix, strip_series_code_affix


def test_strip_series_code_affix_alpha_code():
    assert strip_series_code_affix("SB 01 - StarBridge") == "StarBridge"
    assert strip_series_code_affix("DC 02 - Tears of War") == "Tears of War"


def test_strip_series_code_affix_book_word():
    assert strip_series_code_affix("Bk 15 - Swordsman's Legacy") == "Swordsman's Legacy"
    assert strip_series_code_affix("Book 18 - Agatha Raisin") == "Agatha Raisin"


def test_strip_series_code_affix_leaves_plain_titles_alone():
    assert strip_series_code_affix("Catch-22") == "Catch-22"
    assert strip_series_code_affix("X-Men") == "X-Men"
    assert strip_series_code_affix("Se7en") == "Se7en"


def test_strip_series_code_affix_never_empties():
    assert strip_series_code_affix("AB 12 - 34") == "AB 12 - 34"


def test_strip_series_code_affix_empty():
    assert strip_series_code_affix("") == ""


def test_leading_spaced_separator_is_strong():
    a = parse_sequence_affix("02 - Yendi")
    assert a is not None and a.sequence == 2.0 and a.cleaned == "Yendi" and a.confidence == "strong"


def test_leading_bracket_close_is_strong():
    a = parse_sequence_affix("1) Foo")
    assert a and a.sequence == 1.0 and a.cleaned == "Foo" and a.confidence == "strong"


def test_leading_unspaced_compound_is_weak():
    a = parse_sequence_affix("30-Day Heart Tune-Up")
    assert a and a.sequence == 30.0 and a.cleaned == "Day Heart Tune-Up" and a.confidence == "weak"


def test_leading_dot_separator():
    a = parse_sequence_affix("01. Title")
    assert a and a.sequence == 1.0 and a.cleaned == "Title" and a.confidence == "strong"


def test_decimal_novella_sequence():
    a = parse_sequence_affix("2.5 - Interlude")
    assert a and a.sequence == 2.5 and a.cleaned == "Interlude" and a.confidence == "strong"


def test_trailing_bracketed_is_strong():
    a = parse_sequence_affix("Foo (2)")
    assert a and a.sequence == 2.0 and a.cleaned == "Foo" and a.confidence == "strong"


def test_trailing_spaced_separator_is_strong():
    a = parse_sequence_affix("Foo - 2")
    assert a and a.sequence == 2.0 and a.cleaned == "Foo" and a.confidence == "strong"


def test_trailing_unspaced_is_weak():
    a = parse_sequence_affix("Catch-22")
    assert a and a.sequence == 22.0 and a.cleaned == "Catch" and a.confidence == "weak"


def test_four_digit_year_is_not_an_affix():
    assert parse_sequence_affix("1984 - Something") is None


def test_no_separator_is_none():
    assert parse_sequence_affix("Fahrenheit 451") is None       # space is not a separator
    assert parse_sequence_affix("2 States") is None


def test_letterless_remainder_is_none():
    assert parse_sequence_affix("05 - ") is None
    assert parse_sequence_affix("3 - 2") is None


def test_empty_is_none():
    assert parse_sequence_affix("") is None


def test_strip_series_book_suffix_trailing():
    from colophon.core.sequence_affix import strip_series_book_suffix
    assert strip_series_book_suffix("Some Title - Bk01") == "Some Title"
    assert strip_series_book_suffix("Some Title - Book 1") == "Some Title"
    assert strip_series_book_suffix("Silent Songs: Book 12") == "Silent Songs"


def test_strip_series_book_suffix_leaves_plain_titles():
    from colophon.core.sequence_affix import strip_series_book_suffix
    assert strip_series_book_suffix("Rainbow Six") == "Rainbow Six"
    assert strip_series_book_suffix("Fahrenheit 451") == "Fahrenheit 451"   # trailing bare number kept
    assert strip_series_book_suffix("Book 1") == "Book 1"                   # nothing but the affix -> unchanged
