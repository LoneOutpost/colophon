from pathlib import Path

from colophon.adapters.audio import AUDIO_EXTENSIONS, is_audio_file, probe_audio_file


def test_audio_extensions_cover_common_formats():
    assert {".mp3", ".m4a", ".m4b"} <= AUDIO_EXTENSIONS


def test_is_audio_file_is_case_insensitive():
    assert is_audio_file(Path("/x/Chapter.MP3"))
    assert not is_audio_file(Path("/x/cover.jpg"))


def test_probe_returns_source_file_with_metadata(make_audio):
    path = make_audio("01.mp3", seconds=1)
    sf = probe_audio_file(path)
    assert sf.path == path
    assert sf.ext == "mp3"
    assert sf.size > 0
    assert sf.duration_seconds > 0.5  # ~1s of silence
