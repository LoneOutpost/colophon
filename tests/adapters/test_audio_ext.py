from pathlib import Path

from colophon.adapters.audio import is_audio_file


def test_mp4_is_not_audio_but_m4b_is():
    assert not is_audio_file(Path("x/video.mp4"))   # mp4 is a video container
    assert is_audio_file(Path("x/book.m4b"))
    assert is_audio_file(Path("x/book.m4a"))
    assert is_audio_file(Path("x/ch1.mp3"))
