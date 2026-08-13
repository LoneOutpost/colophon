from __future__ import annotations

from colophon.core.number_pair import extract_enumeration


def test_of_pair_is_isolated_leaving_the_name():
    residue, pairs = extract_enumeration("1 of 8 Diana Gabaldon")
    assert [(p.n, p.m) for p in pairs] == [(1, 8)]
    assert residue == "Diana Gabaldon"


def test_slash_pair():
    residue, pairs = extract_enumeration("Fiery Cross 3/8")
    assert [(p.n, p.m) for p in pairs] == [(3, 8)]
    assert residue == "Fiery Cross"


def test_four_digit_number_is_never_a_component():
    # "2001 - A Space Odyssey": the year is not a count; no pair, value untouched.
    residue, pairs = extract_enumeration("2001 - A Space Odyssey")
    assert pairs == []
    assert residue == "2001 - A Space Odyssey"


def test_english_of_without_digits_is_not_a_pair():
    # "The Coming of the Ship" must not read "of" as an enumeration connector.
    _, pairs = extract_enumeration("The Coming of the Ship")
    assert pairs == []


def test_bare_hyphen_pair_is_left_to_the_cohort_axiom():
    # "01-28" (ambiguous range/disc-track) is NOT claimed here by design.
    _, pairs = extract_enumeration("01-28 The Coming")
    assert pairs == []


def test_no_pair_returns_value_unchanged():
    assert extract_enumeration("Top 100 Sci-Fi Books") == ("Top 100 Sci-Fi Books", [])


def test_empty():
    assert extract_enumeration("") == ("", [])
    assert extract_enumeration(None) == ("", [])


def test_sequence_pairs_broad_separators_and_underscores():
    from colophon.core.number_pair import extract_sequence_pairs
    # the Gibran glued form '01_28_...' -> (1, 28); distinct chapter text ignored
    assert [(p.n, p.m) for p in extract_sequence_pairs("01_28_The_Coming_of_the_Ship")] == [(1, 28)]
    assert [(p.n, p.m) for p in extract_sequence_pairs("Disk 1 within 22 discs")] == [(1, 22)]
    assert [(p.n, p.m) for p in extract_sequence_pairs("1-22")] == [(1, 22)]
    assert [(p.n, p.m) for p in extract_sequence_pairs("3 out of 8")] == [(3, 8)]
    # 4-digit years never participate
    assert extract_sequence_pairs("2001 2010") == []
