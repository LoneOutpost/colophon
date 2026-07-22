import pytest

from colophon.core.track_index import TrackIndex, parse_track_index


@pytest.mark.parametrize("value,expected", [
    ("01", TrackIndex((1,))),
    ("1", TrackIndex((1,))),
    ("003", TrackIndex((3,))),
    ("183", TrackIndex((183,))),
    ("051 - Stephen King - The Dark Half", TrackIndex((51,))),
    ("096_Desperation", TrackIndex((96,))),
    ("15_The_Stand_Uncut", TrackIndex((15,))),
    ("051-Stephen", TrackIndex((51,))),
    ("83Cujo", TrackIndex((83,))),
    ("01Cujo", TrackIndex((1,))),
    ("06b", TrackIndex((6,), "b")),
    ("07a - Everything's Eventual", TrackIndex((7,), "a")),
    ("02-01", TrackIndex((2, 1))),
    ("1-05", TrackIndex((1, 5))),
    ("cd01-03", TrackIndex((1, 3))),
    ("d2t01", TrackIndex((2, 1))),
    ("disc2-05", TrackIndex((2, 5))),
    ("12.5", TrackIndex((12,), "5")),
    ("Chapter 03", None),
    ("Part 1", None),
    ("1984", None),
    ("Isard's", None),
    ("Intro", None),
    ("", None),
])
def test_parse_track_index(value, expected):
    assert parse_track_index(value) == expected


def test_zero_padding_is_value_equal():
    assert parse_track_index("01") == parse_track_index("1")


def test_ordering_subpart_after_bare_and_before_next():
    assert parse_track_index("1a") < parse_track_index("1b") < parse_track_index("2")
    assert parse_track_index("12") < parse_track_index("12.5") < parse_track_index("13")
    assert parse_track_index("1-02") < parse_track_index("2-01")


def test_parse_track_indices_strips_shared_marker():
    from colophon.core.track_index import parse_track_indices
    assert parse_track_indices(["Chapter 01", "Chapter 02", "Chapter 03"]) == [
        TrackIndex((1,)), TrackIndex((2,)), TrackIndex((3,)),
    ]


def test_parse_track_indices_keeps_unshared_marker_unparsed():
    from colophon.core.track_index import parse_track_indices
    assert parse_track_indices(["Chapter 01", "Epilogue"]) == [None, None]


def test_parse_track_indices_marker_that_is_title_text_is_not_stripped():
    from colophon.core.track_index import parse_track_indices
    assert parse_track_indices(["Chapter of Secrets", "Part 2"]) == [None, None]


def test_parse_track_indices_bare_numbers_pass_through():
    from colophon.core.track_index import parse_track_indices
    assert parse_track_indices(["01", "02", "03"]) == [
        TrackIndex((1,)), TrackIndex((2,)), TrackIndex((3,)),
    ]
