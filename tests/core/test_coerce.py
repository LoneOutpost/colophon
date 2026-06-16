from colophon.core.coerce import to_float, to_int, year_or_none


def test_to_int_parses_valid():
    assert to_int("5") == 5


def test_to_int_returns_none_on_invalid():
    assert to_int("x") is None


def test_to_int_returns_none_on_none():
    assert to_int(None) is None


def test_to_float_parses_decimal():
    assert to_float("1.5") == 1.5


def test_to_float_returns_none_on_invalid():
    assert to_float("x") is None


def test_year_or_none_extracts_year_prefix():
    assert year_or_none("2021-05") == 2021


def test_year_or_none_returns_none_on_none():
    assert year_or_none(None) is None
