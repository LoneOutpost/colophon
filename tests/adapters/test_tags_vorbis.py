import shutil
import subprocess
from pathlib import Path

import mutagen
import pytest

from colophon.adapters.tags import read_embedded_tags, tags_from_loaded, write_embedded_tags
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
