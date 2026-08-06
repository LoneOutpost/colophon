import base64
import shutil
import subprocess
from pathlib import Path

import mutagen
import pytest

from colophon.adapters.tags import (
    embed_cover,
    read_embedded_tags,
    tags_from_loaded,
    write_embedded_tags,
)
from colophon.core.errors import TagWriteError
from colophon.core.models import EmbeddedTags

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="ffmpeg required to generate vorbis fixtures")

_FORMATS = [".opus", ".ogg", ".flac"]


def _silent(path: Path) -> Path:
    """A tiny (0.1s) silent audio file whose codec is chosen by `path`'s suffix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", "0.1", str(path), "-y"],
        check=True,
    )
    return path


@pytest.mark.parametrize("ext", _FORMATS)
def test_reads_tags_written_by_an_external_tool(tmp_path, ext):
    # Tag with raw mutagen (simulating another tagger), then read through our adapter.
    p = _silent(tmp_path / f"book{ext}")
    audio = mutagen.File(p)
    if audio.tags is None:
        audio.add_tags()
    audio["TITLE"] = ["Viper Strike"]
    audio["ARTIST"] = ["Keith Douglass"]
    audio["SERIES"] = ["Carrier"]
    audio["SERIES-PART"] = ["2"]
    audio["DATE"] = ["1996"]
    audio["TRACKNUMBER"] = ["3/12"]
    audio.save()

    tags = read_embedded_tags(p)
    assert tags.title == "Viper Strike"
    assert tags.artist == "Keith Douglass"
    assert tags.series == "Carrier"
    assert tags.sequence == 2.0
    assert tags.year == 1996
    assert tags.track == 3


@pytest.mark.parametrize("ext", _FORMATS)
def test_tags_from_loaded_matches_read_embedded_tags(tmp_path, ext):
    # The scan path (tags_from_loaded) must not drift from the direct read.
    p = _silent(tmp_path / f"book{ext}")
    audio = mutagen.File(p)
    if audio.tags is None:
        audio.add_tags()
    audio["TITLE"] = ["Foundation and Earth"]
    audio.save()

    assert tags_from_loaded(mutagen.File(p), p) == read_embedded_tags(p)
    assert read_embedded_tags(p).title == "Foundation and Earth"


def _full_tags() -> EmbeddedTags:
    return EmbeddedTags(
        title="Viper Strike", album="Carrier", artist="Keith Douglass",
        narrator="Frank Muller", series="Carrier", sequence=2.0, year=1996,
        genre="Science Fiction", description="A book.", asin="B000XYZ", isbn="123", track=3,
    )


@pytest.mark.parametrize("ext", _FORMATS)
def test_write_then_read_round_trips_every_field(tmp_path, ext):
    p = _silent(tmp_path / f"book{ext}")
    write_embedded_tags(p, _full_tags())
    assert read_embedded_tags(p) == _full_tags()


@pytest.mark.parametrize("ext", _FORMATS)
def test_write_none_clears_a_previously_set_field(tmp_path, ext):
    p = _silent(tmp_path / f"book{ext}")
    write_embedded_tags(p, _full_tags())
    write_embedded_tags(p, EmbeddedTags(title="Only Title"))
    got = read_embedded_tags(p)
    assert got.title == "Only Title"
    assert got.series is None and got.sequence is None and got.track is None


@pytest.mark.parametrize("ext", _FORMATS)
def test_write_leaves_unmanaged_tags_intact(tmp_path, ext):
    p = _silent(tmp_path / f"book{ext}")
    audio = mutagen.File(p)
    if audio.tags is None:
        audio.add_tags()
    audio["COPYRIGHT"] = ["ACME"]     # not a managed field
    audio.save()

    write_embedded_tags(p, _full_tags())
    assert mutagen.File(p).get("COPYRIGHT") == ["ACME"]


def test_corrupt_vorbis_file_reads_empty_and_write_raises(tmp_path):
    p = tmp_path / "garbage.opus"
    p.write_bytes(b"not an ogg stream at all")
    assert read_embedded_tags(p) == EmbeddedTags()     # unreadable -> empty, no raise
    with pytest.raises(TagWriteError):
        write_embedded_tags(p, EmbeddedTags(title="x"))


def test_tagged_opus_book_identity_comes_from_the_tag(tmp_path):
    from colophon.adapters.config import Config
    from colophon.app_context import AppContext
    from colophon.controller import AppController

    ingest = tmp_path / "audio"
    folder = ingest / "Some Uploader" / "whatever-folder"
    p = _silent(folder / "track.opus")
    audio = mutagen.File(p)
    if audio.tags is None:
        audio.add_tags()
    audio["TITLE"] = ["The Real Title"]
    audio["ARTIST"] = ["The Real Author"]
    audio.save()

    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite",
                                   library_root=tmp_path / "lib", scan_paths=[ingest]))
    try:
        AppController(ctx).scan([ingest])
        book = next(b for b in ctx.books.list_all() if b.source_folder == folder)
        assert book.title == "The Real Title", book.title
        assert book.authors == ["The Real Author"], book.authors
    finally:
        ctx.close()


# A 1x1 PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQAY3Y2wAAAAAElFTkSuQmCC")
_PNG2 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _read_cover(path) -> bytes | None:
    from mutagen.flac import Picture
    a = mutagen.File(path)
    if path.suffix.lower() == ".flac":
        return a.pictures[0].data if a.pictures else None
    b64 = a.get("metadata_block_picture")
    return Picture(base64.b64decode(b64[0])).data if b64 else None


@pytest.mark.parametrize("ext", _FORMATS)
def test_embed_cover_round_trips(tmp_path, ext):
    p = _silent(tmp_path / f"book{ext}")
    embed_cover(p, _PNG, "image/png")
    assert _read_cover(p) == _PNG


@pytest.mark.parametrize("ext", _FORMATS)
def test_embed_cover_replaces_existing(tmp_path, ext):
    p = _silent(tmp_path / f"book{ext}")
    embed_cover(p, _PNG, "image/png")
    embed_cover(p, _PNG2, "image/png")
    assert _read_cover(p) == _PNG2
    if ext == ".flac":                      # exactly one picture, not appended
        assert len(mutagen.File(p).pictures) == 1


def test_embed_cover_corrupt_vorbis_raises(tmp_path):
    p = tmp_path / "garbage.opus"
    p.write_bytes(b"not an ogg stream at all")
    with pytest.raises(TagWriteError):
        embed_cover(p, _PNG, "image/png")
