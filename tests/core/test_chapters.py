from colophon.core.chapters import Chapter, file_boundary_chapters, to_ffmetadata


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
