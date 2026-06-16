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
