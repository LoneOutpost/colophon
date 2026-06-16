from colophon.core.match import ratio, title_author_score


def test_ratio_is_case_and_space_insensitive():
    assert ratio("The Way of Kings", "the way of kings") == 1.0
    assert ratio("Dune", "Dune ") == 1.0


def test_ratio_partial():
    assert 0.0 < ratio("Dune", "Dune Messiah") < 1.0


def test_ratio_empty_inputs_are_zero():
    assert ratio("", "x") == 0.0
    assert ratio(None, "x") == 0.0


def test_title_author_score_combines_both():
    perfect = title_author_score("Dune", ["Frank Herbert"], "Dune", ["Frank Herbert"])
    assert perfect == 1.0
    title_only = title_author_score("Dune", ["Frank Herbert"], "Dune", ["Someone Else"])
    assert 0.0 < title_only < 1.0
