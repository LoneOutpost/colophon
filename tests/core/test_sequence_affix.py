from colophon.core.sequence_affix import parse_sequence_affix


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
