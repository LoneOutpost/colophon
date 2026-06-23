from colophon.core.people import split_people


def test_splits_multiple_full_names_on_comma():
    assert split_people("Brandon Sanderson, Janci Patterson") == [
        "Brandon Sanderson", "Janci Patterson",
    ]


def test_keeps_last_first_single_name():
    assert split_people("Herbert, Frank") == ["Herbert, Frank"]


def test_keeps_suffix_name():
    assert split_people("Smith, Jr.") == ["Smith, Jr."]


def test_splits_on_ampersand_and_and_and_semicolon():
    assert split_people("A & B") == ["A", "B"]
    assert split_people("A and B") == ["A", "B"]
    assert split_people("A; B") == ["A", "B"]


def test_ampersand_then_comma_chunk_kept_when_not_full_names():
    assert split_people("A & B, C") == ["A", "B, C"]


def test_does_not_split_the_word_inside_a_name():
    assert split_people("Anderson") == ["Anderson"]


def test_none_and_blank_give_empty():
    assert split_people(None) == []
    assert split_people("") == []
    assert split_people("   ") == []


def test_documented_tradeoff_multiword_surname_last_first_is_split():
    # Known auto-mode limitation: a lone "Multi Word Surname, First" is wrongly
    # split because both parts contain whitespace. Mitigated by a provider hint.
    assert split_people("Le Guin, Ursula K.") == ["Le Guin", "Ursula K."]
