from pathlib import Path

from colophon.adapters.audio import is_audio_file


def test_mp4_is_not_audio_but_m4b_is():
    assert not is_audio_file(Path("x/video.mp4"))   # mp4 is a video container
    assert is_audio_file(Path("x/book.m4b"))
    assert is_audio_file(Path("x/book.m4a"))
    assert is_audio_file(Path("x/ch1.mp3"))


def test_opus_is_audio():
    # .opus was missing from the whitelist, so opus parts were dropped at scan and a mixed
    # mp3/opus book looked like it was missing parts. (.ogg was already recognized.)
    assert is_audio_file(Path("x/part01.opus"))
