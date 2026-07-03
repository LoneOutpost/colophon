from colophon.core.franchise_seeds import DEFAULT_FRANCHISE_NAMES, default_franchises
from colophon.core.graph_resolve import _name_key


def test_default_franchises_are_keyed_by_normalized_name():
    seeds = default_franchises()
    # every display maps under its normalized key, so a folder named any-case matches
    for name in DEFAULT_FRANCHISE_NAMES:
        assert seeds[_name_key(name)] == name


def test_star_wars_and_trek_and_warhammer_are_seeded():
    seeds = default_franchises()
    assert seeds[_name_key("STAR WARS")] == "Star Wars"
    assert seeds[_name_key("star trek")] == "Star Trek"
    assert seeds[_name_key("Warhammer")] == "Warhammer"


def test_no_duplicate_normalized_keys():
    # two display spellings must not collide on one key (a silent seed drop)
    assert len(default_franchises()) == len(DEFAULT_FRANCHISE_NAMES)
