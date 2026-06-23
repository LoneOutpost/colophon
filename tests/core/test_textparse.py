from colophon.core.textparse import parse_narrators, parse_runtime_ms


def test_parse_runtime_ms():
    assert parse_runtime_ms("7:34:27") == 27267000
    assert parse_runtime_ms("58:03") == 3483000
    assert parse_runtime_ms("90") == 90000
    assert parse_runtime_ms("abc") is None
    assert parse_runtime_ms(None) is None


def test_parse_narrators():
    assert parse_narrators("A classic. Read by Jane Doe.") == ["Jane Doe"]
    assert parse_narrators("Narrated by Alice and Bob") == ["Alice", "Bob"]
    assert parse_narrators("Reader: Kara Shallenberg") == ["Kara Shallenberg"]
    assert parse_narrators("No cue here at all") == []
    assert parse_narrators(None) == []
