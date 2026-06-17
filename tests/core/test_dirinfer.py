from pathlib import Path

from colophon.core.dirinfer import infer_from_path, parse_scheme


def test_parse_scheme_lowercases_and_splits():
    assert parse_scheme("Author/Series/Title") == ["author", "series", "title"]
    assert parse_scheme("") == []
    assert parse_scheme("Author / Title") == ["author", "title"]


def test_infer_maps_when_depth_matches_scheme():
    got = infer_from_path(
        Path("/root/Brandon Sanderson/Stormlight Archive/The Way of Kings"),
        Path("/root"), ["author", "series", "title"],
    )
    assert got == {"author": "Brandon Sanderson", "series": "Stormlight Archive", "title": "The Way of Kings"}


def test_infer_returns_empty_when_depth_mismatches():
    assert infer_from_path(Path("/root/Author/Title"), Path("/root"), ["author", "series", "title"]) == {}


def test_infer_empty_scheme_or_outside_root():
    assert infer_from_path(Path("/root/A/B"), Path("/root"), []) == {}
    assert infer_from_path(Path("/other/A/B"), Path("/root"), ["author", "title"]) == {}


def test_infer_skips_unknown_tokens_positionally():
    got = infer_from_path(Path("/root/Brandon/ignored/Kings"), Path("/root"), ["author", "x", "title"])
    assert got == {"author": "Brandon", "title": "Kings"}
