import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg required to generate fixtures")


def test_format_for_maps_extensions_to_handlers():
    from colophon.adapters.audio_formats import (
        Mp3Format,
        Mp4Format,
        VorbisFormat,
        format_for,
    )
    assert isinstance(format_for(".mp3"), Mp3Format)
    assert isinstance(format_for(".M4B"), Mp4Format)     # case-insensitive
    assert isinstance(format_for(".opus"), VorbisFormat)
    assert isinstance(format_for(".flac"), VorbisFormat)
    assert format_for(".wav") is None                    # unknown -> None


def _silent(path: Path, *, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", str(seconds), str(path), "-y"],
        check=True,
    )
    return path


def test_opus_file_gets_a_derived_bitrate_and_codec(tmp_path):
    from colophon.adapters.audio import read_audio_metadata
    sf, _ = read_audio_metadata(_silent(tmp_path / "book.opus"))
    assert sf.bitrate > 0
    assert sf.codec == "Opus"


def test_opus_file_is_known_quality(tmp_path):
    from colophon.adapters.audio import read_audio_metadata
    from colophon.core.audio_quality import _audio_with_quality
    sf, _ = read_audio_metadata(_silent(tmp_path / "book.opus"))
    assert _audio_with_quality([sf]) == [sf]     # bitrate > 0 -> counts toward quality comparison


def test_mp3_bitrate_comes_from_mutagen_not_the_fallback(tmp_path):
    # ffmpeg default mp3 is ~128 kbps; assert a plausible mp3 bitrate (the fallback fires only for opus).
    from colophon.adapters.audio import read_audio_metadata
    p = tmp_path / "book.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "1.0", "-b:a", "128k", str(p), "-y"], check=True)
    sf, _ = read_audio_metadata(p)
    assert 96_000 <= sf.bitrate <= 160_000
    assert sf.codec == "MP3"


def test_read_info_zero_duration_is_zero_bitrate():
    from colophon.adapters.audio_formats import Mp3Format
    info = Mp3Format().read_info(None, size=1000, duration=0.0)
    assert info.bitrate == 0 and info.sample_rate == 0 and info.channels == 0
