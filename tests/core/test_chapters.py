import pytest

from colophon.core.chapters import (
    Chapter,
    file_boundary_chapters,
    format_timecode,
    normalize_chapters,
    parse_timecode,
    shift_chapters,
    to_ffmetadata,
)


def test_file_boundary_chapters_accumulate_timeline():
    chapters = file_boundary_chapters([("01 Intro.mp3", 10.0), ("02 Body.mp3", 20.0)])
    assert chapters == [
        Chapter(title="01 Intro", start_ms=0, end_ms=10_000),
        Chapter(title="02 Body", start_ms=10_000, end_ms=30_000),
    ]


def test_file_boundary_titles_strip_extension():
    chapters = file_boundary_chapters([("Chapter One.m4a", 5.0)])
    assert chapters[0].title == "Chapter One"


def test_to_ffmetadata_emits_chapter_blocks():
    meta = to_ffmetadata([
        Chapter(title="One", start_ms=0, end_ms=1000),
        Chapter(title="Two", start_ms=1000, end_ms=2000),
    ])
    assert meta.startswith(";FFMETADATA1")
    assert meta.count("[CHAPTER]") == 2
    assert "TIMEBASE=1/1000" in meta
    assert "START=1000" in meta
    assert "END=2000" in meta
    assert "title=Two" in meta


def test_to_ffmetadata_escapes_special_chars():
    meta = to_ffmetadata([Chapter(title="A=B; C\\D", start_ms=0, end_ms=10)])
    # '=', ';', '\\', and newlines must be backslash-escaped in ffmetadata values
    assert r"title=A\=B\; C\\D" in meta


def test_format_timecode():
    assert format_timecode(0) == "0:00:00"
    assert format_timecode(61_000) == "0:01:01"
    assert format_timecode(3_661_000) == "1:01:01"
    assert format_timecode(-500) == "0:00:00"


def test_parse_timecode_forms():
    assert parse_timecode("90") == 90_000
    assert parse_timecode("1:30") == 90_000
    assert parse_timecode("1:01:01") == 3_661_000
    assert parse_timecode(" 2:00 ") == 120_000


@pytest.mark.parametrize("bad", ["", "a:b", "1:2:3:4", "-5", "1:-2"])
def test_parse_timecode_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_timecode(bad)


def test_parse_format_round_trip():
    for ms in (0, 90_000, 3_661_000):
        assert parse_timecode(format_timecode(ms)) == ms


def test_normalize_sorts_and_recomputes_ends():
    chs = [
        Chapter(title="b", start_ms=20_000, end_ms=999),
        Chapter(title="a", start_ms=0, end_ms=5),
        Chapter(title="c", start_ms=40_000, end_ms=1),
    ]
    out = normalize_chapters(chs, total_ms=60_000)
    assert [(c.title, c.start_ms, c.end_ms) for c in out] == [
        ("a", 0, 20_000),
        ("b", 20_000, 40_000),
        ("c", 40_000, 60_000),
    ]


def test_normalize_clamps_start_into_range():
    out = normalize_chapters(
        [Chapter(title="x", start_ms=-1000, end_ms=0),
         Chapter(title="y", start_ms=90_000, end_ms=0)],
        total_ms=60_000,
    )
    assert [(c.start_ms, c.end_ms) for c in out] == [(0, 60_000), (60_000, 60_000)]


def test_shift_chapters_clamps_at_zero():
    chs = [
        Chapter(title="a", start_ms=5_000, end_ms=10_000),
        Chapter(title="b", start_ms=10_000, end_ms=20_000),
    ]
    out = shift_chapters(chs, delta_ms=-8_000, total_ms=60_000)
    assert [(c.title, c.start_ms) for c in out] == [("a", 0), ("b", 2_000)]
