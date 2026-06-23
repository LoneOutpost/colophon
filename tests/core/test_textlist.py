from colophon.core.textlist import dedupe_preserving, join_list, split_list


def test_split_list_trims_and_drops_empties():
    assert split_list("a; b ;; c ") == ["a", "b", "c"]


def test_split_list_none_and_blank_give_empty():
    assert split_list(None) == []
    assert split_list("") == []
    assert split_list("  ;  ") == []


def test_split_list_custom_separator():
    assert split_list("a, b , c", sep=",") == ["a", "b", "c"]


def test_join_list_uses_default_separator():
    assert join_list(["a", "b", "c"]) == "a; b; c"


def test_join_list_empty_is_none():
    assert join_list([]) is None


def test_join_list_custom_separator():
    assert join_list(["a", "b"], sep=" / ") == "a / b"


def test_dedupe_preserving_keeps_first_seen_order():
    assert dedupe_preserving(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_dedupe_preserving_with_casefold_key():
    assert dedupe_preserving(["Sci-Fi", "sci-fi", "Fantasy"], key=str.casefold) == [
        "Sci-Fi",
        "Fantasy",
    ]
