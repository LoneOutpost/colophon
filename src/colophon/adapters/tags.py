"""Read and write embedded tags, dispatched to a per-format handler (see audio_formats)."""

from __future__ import annotations

from pathlib import Path

from colophon.adapters.audio_formats import format_for
from colophon.core.errors import TagWriteError
from colophon.core.models import EmbeddedTags


def read_embedded_tags(path: Path) -> EmbeddedTags:
    """Open `path` and extract its embedded tags. An unsupported/failed read yields empty tags."""
    fmt = format_for(path.suffix)
    return fmt.read_tags(path) if fmt is not None else EmbeddedTags()


def tags_from_loaded(audio, path: Path) -> EmbeddedTags:
    """Extract EmbeddedTags from an object already loaded by `mutagen.File(path)`. None / unsupported
    extension -> empty tags."""
    fmt = format_for(path.suffix)
    if fmt is None or audio is None:
        return EmbeddedTags()
    return fmt.tags_from_loaded(audio)


def write_embedded_tags(path: Path, tags: EmbeddedTags) -> None:
    """Write `tags` into the audio file at `path`. Raises TagWriteError on an unsupported format or a
    mutagen failure; managed fields mirror `tags` (a None clears its field)."""
    fmt = format_for(path.suffix)
    try:
        if fmt is None:
            raise TagWriteError(f"unsupported audio format for writing: {path.suffix.lower()}")
        fmt.write_tags(path, tags)
    except TagWriteError:
        raise
    except Exception as e:
        raise TagWriteError(f"write tags to {path} failed: {e}") from e


def embed_cover(path: Path, image_bytes: bytes, mime: str) -> None:
    """Embed cover art (mime 'image/png' or 'image/jpeg'), replacing any existing cover. Raises
    TagWriteError on an unsupported format or a mutagen failure."""
    fmt = format_for(path.suffix)
    try:
        if fmt is None:
            raise TagWriteError(f"unsupported audio format for cover: {path.suffix.lower()}")
        fmt.embed_cover(path, image_bytes, mime)
    except TagWriteError:
        raise
    except Exception as e:
        raise TagWriteError(f"embed cover into {path} failed: {e}") from e
