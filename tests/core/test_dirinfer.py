from pathlib import Path

from colophon.core.dirinfer import infer_from_path, parse_scheme


def test_parse_scheme_compiles_levels():
    assert len(parse_scheme("$Author/$Series/$Title")) == 3
    assert parse_scheme("") == []


def test_infer_maps_when_depth_matches():
    got = infer_from_path(
        Path("/root/Brandon Sanderson/Stormlight Archive/The Way of Kings"),
        Path("/root"), parse_scheme("$Author/$Series/$Title"),
    )
    assert got == {
        "author": "Brandon Sanderson", "series": "Stormlight Archive", "title": "The Way of Kings",
    }


def test_infer_multi_field_level():
    got = infer_from_path(
        Path("/root/Brandon Sanderson/Stormlight #1/The Way of Kings"),
        Path("/root"), parse_scheme("$Author/$Series #$SerNum/$Title"),
    )
    assert got["series"] == "Stormlight" and got["sequence"] == "1"
    assert got["author"] == "Brandon Sanderson" and got["title"] == "The Way of Kings"


def test_skip_level_ignored():
    got = infer_from_path(
        Path("/root/Brandon/ignore-me/Kings"), Path("/root"),
        parse_scheme("$Author/$Skip/$Title"),
    )
    assert got == {"author": "Brandon", "title": "Kings"}


def test_non_matching_level_contributes_nothing():
    got = infer_from_path(  # the "#$SerNum" level has no '#' in the folder
        Path("/root/Brandon/Stormlight/Kings"), Path("/root"),
        parse_scheme("$Author/$Series #$SerNum/$Title"),
    )
    assert got == {"author": "Brandon", "title": "Kings"}


def test_dotted_component_not_truncated():
    got = infer_from_path(
        Path("/root/Stormlight 2.5"), Path("/root"), parse_scheme("$Series"),
    )
    assert got == {"series": "Stormlight 2.5"}  # raw match, no extension stripping


def test_depth_mismatch_or_empty_or_outside_root():
    assert infer_from_path(Path("/root/A/B"), Path("/root"), parse_scheme("$Author/$Series/$Title")) == {}
    assert infer_from_path(Path("/root/A/B"), Path("/root"), parse_scheme("")) == {}
    assert infer_from_path(Path("/other/A/B"), Path("/root"), parse_scheme("$Author/$Title")) == {}
