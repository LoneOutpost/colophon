from pathlib import Path

from colophon.core.audio_quality import book_quality_summary, codec_label, format_file_quality
from colophon.core.models import SourceFile


def test_codec_label_maps_common_extensions():
    assert codec_label("mp3") == "MP3"
    assert codec_label("m4b") == "M4B"
    assert codec_label("m4a") == "AAC"
    assert codec_label("flac") == "FLAC"
    assert codec_label("opus") == "Opus"
    assert codec_label("ogg") == "OGG"


def test_codec_label_unknown_extension_uppercases():
    assert codec_label("wma") == "WMA"
    assert codec_label("") == ""


def test_codec_label_tolerates_leading_dot():
    assert codec_label(".mp3") == "MP3"
    assert codec_label(".") == ""


def _sf(name, *, bitrate=0, sample_rate=0, channels=0, codec="", dur=60.0):
    return SourceFile(path=Path(name), size=1000, duration_seconds=dur, ext=codec.lower() or "mp3",
                      bitrate=bitrate, sample_rate=sample_rate, channels=channels, codec=codec)


def test_format_file_quality_full():
    sf = _sf("a.mp3", bitrate=128000, sample_rate=44100, channels=2, codec="MP3")
    assert format_file_quality(sf) == "128 kbps · 44.1 kHz · stereo · MP3"


def test_format_file_quality_mono_and_partial():
    sf = _sf("a.mp3", bitrate=64000, sample_rate=22050, channels=1, codec="MP3")
    assert format_file_quality(sf) == "64 kbps · 22.05 kHz · mono · MP3"
    sf2 = _sf("a.mp3", bitrate=0, sample_rate=0, channels=0, codec="")  # all unknown
    assert format_file_quality(sf2) == ""


def test_book_quality_summary_uniform_and_mixed_and_none():
    uniform = [_sf("1.mp3", bitrate=128000, sample_rate=44100, channels=2, codec="MP3"),
               _sf("2.mp3", bitrate=129000, sample_rate=44100, channels=2, codec="MP3")]  # same tier
    assert book_quality_summary(uniform) == "128 kbps MP3"
    mixed = [_sf("1.mp3", bitrate=64000, sample_rate=44100, channels=2, codec="MP3"),
             _sf("2.mp3", bitrate=128000, sample_rate=44100, channels=2, codec="MP3")]
    assert book_quality_summary(mixed) == "Mixed quality"
    assert book_quality_summary([_sf("1.mp3")]) is None  # no known quality
