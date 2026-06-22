from pathlib import Path

import pytest
from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, TXXX

from colophon.adapters.tags import read_embedded_tags, write_embedded_tags
from colophon.core.errors import TagWriteError
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
    id3.add(TXXX(encoding=3, desc="isbn", text=["9780306406157"]))
    id3.save(path)

    tags = read_embedded_tags(path)
    assert tags.title == "The Way of Kings"
    assert tags.artist == "Brandon Sanderson"
    assert tags.series == "Stormlight Archive"
    assert tags.sequence == 1.0
    assert tags.asin == "B0041JKFJW"
    assert tags.isbn == "9780306406157"


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


def test_write_then_read_roundtrips_mp3(tmp_path: Path):
    path = tmp_path / "ch01.mp3"
    path.write_bytes(b"")
    tags = EmbeddedTags(
        title="The Way of Kings", album="The Way of Kings", artist="Brandon Sanderson",
        narrator="Michael Kramer; Kate Reading", series="Stormlight Archive", sequence=1.0,
        year=2010, genre="Fantasy", description="A long book.", asin="B0041JKFJW",
        isbn="9780306406157",
    )
    write_embedded_tags(path, tags)
    assert read_embedded_tags(path) == tags


def test_write_then_read_roundtrips_mp4(make_audio):
    path = make_audio("book.m4b", seconds=1)
    tags = EmbeddedTags(
        title="Mistborn", album="Mistborn", artist="Brandon Sanderson", narrator="Michael Kramer",
        series="Mistborn", sequence=1.0, year=2006, genre="Fantasy",
        description="Heist with magic.", asin="B002UZMUVK", isbn="9780765311788",
    )
    write_embedded_tags(path, tags)
    assert read_embedded_tags(path) == tags


def test_write_unsupported_format_raises(tmp_path: Path):
    path = tmp_path / "song.ogg"
    path.write_bytes(b"")
    with pytest.raises(TagWriteError):
        write_embedded_tags(path, EmbeddedTags(title="x"))


_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae426082"
)


def test_embed_cover_roundtrips_mp3(tmp_path: Path):
    from mutagen.id3 import ID3

    from colophon.adapters.tags import embed_cover
    path = tmp_path / "ch01.mp3"
    path.write_bytes(b"")
    embed_cover(path, _PNG_1X1, "image/png")
    apic = ID3(path).getall("APIC")
    assert apic and apic[0].data == _PNG_1X1 and apic[0].mime == "image/png"


def test_embed_cover_roundtrips_mp4(make_audio):
    from mutagen.mp4 import MP4

    from colophon.adapters.tags import embed_cover
    path = make_audio("book.m4b", seconds=1)
    embed_cover(path, _PNG_1X1, "image/png")
    covr = MP4(path).get("covr")
    assert covr and bytes(covr[0]) == _PNG_1X1


def test_embed_cover_unsupported_format_raises(tmp_path: Path):
    from colophon.adapters.tags import embed_cover
    path = tmp_path / "song.ogg"
    path.write_bytes(b"")
    with pytest.raises(TagWriteError):
        embed_cover(path, _PNG_1X1, "image/png")


def test_write_clears_managed_field_set_to_none_mp3(tmp_path: Path):
    path = tmp_path / "ch.mp3"
    path.write_bytes(b"")
    write_embedded_tags(path, EmbeddedTags(title="Set", artist="A", series="S"))
    assert read_embedded_tags(path).title == "Set"
    # Re-write with title/artist/series None -> those managed fields are cleared.
    write_embedded_tags(path, EmbeddedTags(title=None, artist=None, series=None))
    cleared = read_embedded_tags(path)
    assert cleared.title is None and cleared.artist is None and cleared.series is None


def test_write_clears_managed_field_set_to_none_mp4(make_audio):
    path = make_audio("ch.m4b", seconds=1)
    write_embedded_tags(path, EmbeddedTags(title="Set", narrator="N"))
    assert read_embedded_tags(path).title == "Set"
    write_embedded_tags(path, EmbeddedTags(title=None, narrator=None))
    cleared = read_embedded_tags(path)
    assert cleared.title is None and cleared.narrator is None


def test_write_clears_legacy_cmt_description_atom_mp4(make_audio):
    from mutagen.mp4 import MP4

    path = make_audio("ch.m4b", seconds=1)
    m = MP4(path)
    m["\xa9cmt"] = ["legacy comment"]  # description sourced from the legacy atom
    m.save()
    assert read_embedded_tags(path).description == "legacy comment"
    # A managed write with description=None must clear it (not leave \xa9cmt shadowing).
    write_embedded_tags(path, EmbeddedTags(title="X"))
    assert read_embedded_tags(path).description is None
