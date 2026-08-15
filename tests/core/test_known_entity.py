from colophon.core.known_entity import build_known_series, match_known_series


def test_build_known_series_drops_junk():
    known = build_known_series(["Quiller", "Pern", "Book", "01 of", "1", "Part", "series", "Culture"])
    assert set(known.values()) == {"Quiller", "Pern", "Culture"}   # junk dropped


def test_build_known_series_drops_bare_sequence_markers():
    # a shattered folder segment can leave a bare 'Bk'/'Vol'/'Disc' that is never a series name
    known = build_known_series(["Eve Dallas", "Bk", "Vol", "Volume", "Disc", "CD"])
    assert set(known.values()) == {"Eve Dallas"}


def test_match_known_series_normalized_exact():
    known = build_known_series(["Quiller", "Rogue Angel"])
    assert match_known_series(["Adam Hall.-.Quiller Bk03.-.The Striker Portfolio"], known) == "Quiller"
    # bracket is a separator, not an indicator
    assert match_known_series(["Todd - [Rogue Angel] - Some Title"], known) == "Rogue Angel"
    assert match_known_series(["No Known Series Here.-.A Title"], known) is None


def test_match_uses_name_nn_and_respects_exclusion():
    known = build_known_series(["Culture", "Inversions"])
    folder = "Iain M Banks.-.Culture 06.-.Inversions"
    # without exclusion, the title token 'Inversions' would wrongly match a known series
    assert match_known_series([folder], known) == "Culture"           # 'Culture 06' -> 'Culture' wins first
    # with the title excluded, 'Inversions' can never be chosen even if order differed
    assert match_known_series(["Iain M Banks.-.Inversions.-.Culture 06"], known,
                              exclude=["Inversions", "Iain M Banks"]) == "Culture"


def test_author_and_title_tokens_are_excluded():
    known = build_known_series(["Forsworn"])   # a mis-derived title-as-series in the library
    # the real title 'Forsworn' must not be picked as this book's series once excluded
    assert match_known_series(["Brian McClellan.-.Powder Mage.-.Forsworn"], known,
                              exclude=["Forsworn"]) is None
