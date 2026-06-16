from pathlib import Path

from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TXXX

from colophon.adapters.tags import read_embedded_tags
from colophon.core.models import EmbeddedTags


def test_unreadable_file_yields_empty_tags(tmp_path: Path):
    junk = tmp_path / "not-audio.mp3"
    junk.write_bytes(b"not an mp3")
    assert read_embedded_tags(junk) == EmbeddedTags()


def test_reads_id3_frames_from_mp3(tmp_path: Path):
    path = tmp_path / "ch01.mp3"
    path.write_bytes(b"")
    id3 = ID3()
    id3.add(TIT2(encoding=3, text=["The Way of Kings"]))
    id3.add(TALB(encoding=3, text=["The Way of Kings"]))
    id3.add(TPE1(encoding=3, text=["Brandon Sanderson"]))
    id3.add(TXXX(encoding=3, desc="series", text=["Stormlight Archive"]))
    id3.add(TXXX(encoding=3, desc="sequence", text=["1"]))
    id3.add(TXXX(encoding=3, desc="asin", text=["B0041JKFJW"]))
    id3.save(path)

    tags = read_embedded_tags(path)
    assert tags.title == "The Way of Kings"
    assert tags.artist == "Brandon Sanderson"
    assert tags.series == "Stormlight Archive"
    assert tags.sequence == 1.0
    assert tags.asin == "B0041JKFJW"


def test_reads_comm_description_from_mp3(tmp_path: Path):
    path = tmp_path / "ch01.mp3"
    path.write_bytes(b"")
    id3 = ID3()
    id3.add(COMM(encoding=3, lang="eng", desc="", text=["A description"]))
    id3.save(path)

    assert read_embedded_tags(path).description == "A description"


def test_reads_tags_from_m4b(make_audio):
    from mutagen.mp4 import MP4

    path = make_audio("book.m4b", seconds=1)
    m = MP4(path)
    m["\xa9nam"] = ["Dune"]
    m["\xa9ART"] = ["Frank Herbert"]
    m.save()

    tags = read_embedded_tags(path)
    assert tags.title == "Dune"
    assert tags.artist == "Frank Herbert"
