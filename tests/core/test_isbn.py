from colophon.core.isbn import isbn_equal, normalize_isbn, to_isbn13


def test_normalize():
    assert normalize_isbn("978-0-13-468599-1") == "9780134685991"
    assert normalize_isbn(" 0306406152 ") == "0306406152"
    assert normalize_isbn("080442957x") == "080442957X"
    assert normalize_isbn("") is None
    assert normalize_isbn(None) is None


def test_to_isbn13():
    assert to_isbn13("9780134685991") == "9780134685991"
    assert to_isbn13("0306406152") == "9780306406157"
    assert to_isbn13("080442957X") == "9780804429573"
    assert to_isbn13("notanisbn") is None
    assert to_isbn13(None) is None


def test_isbn_equal():
    assert isbn_equal("0306406152", "9780306406157") is True
    assert isbn_equal("978-0-306-40615-7", "9780306406157") is True
    assert isbn_equal("9780134685991", "9780306406157") is False
    assert isbn_equal(None, "9780306406157") is False
