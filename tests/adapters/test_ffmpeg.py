import pytest

from colophon.adapters.ffmpeg import (
    FFmpegError,
    concat_encode,
    probe_chapter_count,
    probe_codec,
    probe_duration_seconds,
)
from colophon.core.chapters import file_boundary_chapters, to_ffmetadata


def test_probe_duration_of_silence(make_audio):
    path = make_audio("a.mp3", seconds=2)
    dur = probe_duration_seconds(path)
    assert 1.5 < dur < 2.6


def test_probe_codec_mp3(make_audio):
    path = make_audio("a.mp3", seconds=1)
    assert probe_codec(path) == "mp3"


def test_probe_codec_m4b_is_aac(make_audio):
    path = make_audio("a.m4b", seconds=1)
    assert probe_codec(path) == "aac"


def test_probe_missing_file_raises(tmp_path):
    with pytest.raises(FFmpegError):
        probe_duration_seconds(tmp_path / "nope.mp3")


def test_concat_encode_transcodes_two_mp3_to_m4b(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=1)
    b = make_audio("02.mp3", seconds=1)
    chapters = file_boundary_chapters([("01.mp3", 1.0), ("02.mp3", 1.0)])
    meta = tmp_path / "meta.txt"
    meta.write_text(to_ffmetadata(chapters))
    out = tmp_path / "book.m4b"

    concat_encode([a, b], out, metadata_path=meta, codec="aac", bitrate="64k")

    assert out.exists() and out.stat().st_size > 0
    assert probe_codec(out) == "aac"
    assert 1.6 < probe_duration_seconds(out) < 2.8


def test_concat_encode_embeds_two_chapters(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=1)
    b = make_audio("02.mp3", seconds=1)
    chapters = file_boundary_chapters([("01.mp3", 1.0), ("02.mp3", 1.0)])
    meta = tmp_path / "meta.txt"
    meta.write_text(to_ffmetadata(chapters))
    out = tmp_path / "book.m4b"

    concat_encode([a, b], out, metadata_path=meta, codec="aac", bitrate="64k")

    assert probe_chapter_count(out) == 2


def test_concat_encode_bad_input_raises(tmp_path, make_audio):
    meta = tmp_path / "meta.txt"
    meta.write_text(";FFMETADATA1\n")
    with pytest.raises(FFmpegError):
        concat_encode([tmp_path / "missing.mp3"], tmp_path / "o.m4b", metadata_path=meta, codec="aac", bitrate="64k")


def test_concat_encode_joins_heterogeneous_mp3_and_opus(make_audio, tmp_path):
    # A book mixing mp3 (22.05 kHz) and opus (always 48 kHz) must transcode to one m4b with BOTH
    # parts. The concat DEMUXER treats the list as one stream with the first input's codec and
    # silently drops the opus segment (decode fails), truncating the output; the concat FILTER
    # decodes+resamples each input and joins them correctly.
    a = make_audio("01.mp3", seconds=2)
    b = make_audio("02.opus", seconds=2)
    chapters = file_boundary_chapters([("01.mp3", 2.0), ("02.opus", 2.0)])
    meta = tmp_path / "meta.txt"
    meta.write_text(to_ffmetadata(chapters))
    out = tmp_path / "book.m4b"

    concat_encode([a, b], out, metadata_path=meta, codec="aac", bitrate="64k")

    assert out.exists() and probe_codec(out) == "aac"
    assert 3.4 < probe_duration_seconds(out) < 4.6   # BOTH 2s parts present, not just the mp3
    assert probe_chapter_count(out) == 2             # chapters preserved through the filter path


def test_parse_progress_us_reads_out_time_us_and_timestamp():
    from colophon.adapters.ffmpeg import _parse_progress_us
    assert _parse_progress_us("out_time_us=1500000") == 1_500_000
    assert _parse_progress_us("out_time=00:00:02.500000") == 2_500_000
    assert _parse_progress_us("out_time=01:02:03") == (3600 + 120 + 3) * 1_000_000
    assert _parse_progress_us("frame=12") is None
    assert _parse_progress_us("out_time_us=N/A") is None


def test_concat_encode_streams_progress(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=2)
    b = make_audio("02.mp3", seconds=2)
    meta = tmp_path / "m.ffmeta"
    meta.write_text(to_ffmetadata(file_boundary_chapters([("01.mp3", 2.0), ("02.mp3", 2.0)])))
    out = tmp_path / "out.m4b"
    seen: list[float] = []
    concat_encode([a, b], out, metadata_path=meta, codec="aac", bitrate="64k",
                  total_seconds=4.0, on_progress=seen.append)
    assert out.exists()
    assert seen, "expected at least one progress callback"
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert max(seen) > 0.0
