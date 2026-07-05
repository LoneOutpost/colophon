"""Filesystem-level audio inspection: extensions and per-file probing."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import MutagenError

from colophon.adapters.ffmpeg import FFmpegError, probe_duration_seconds
from colophon.adapters.tags import read_embedded_tags, tags_from_loaded
from colophon.core.models import EmbeddedTags, SourceFile

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".flac"}

AUDIO_META_CACHE_SIZE = 4096  # bounds memory; within a scan the 3 reads of a file are
# consecutive (one book's processing), so the dedup win does not need a large cache — this
# size also lets re-scans of recently-seen files skip the parse.


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def read_audio_metadata(path: Path) -> tuple[SourceFile, EmbeddedTags]:
    """Open `path` exactly once and return both its SourceFile (size, duration, bare ext)
    and its EmbeddedTags. Memoized on (path, st_mtime_ns, st_size): an unchanged file is
    served from memory; a changed file (including one a tag-write just touched) is re-read.

    The returned value objects are treated as immutable (no caller mutates SourceFile /
    EmbeddedTags fields — verified across the codebase), so the cached instances are shared.
    A future caller needing to mutate one must `.model_copy()` first.
    """
    st = path.stat()
    return _read_audio_metadata(str(path), st.st_mtime_ns, st.st_size, path)


@lru_cache(maxsize=AUDIO_META_CACHE_SIZE)
def _read_audio_metadata(
    path_str: str, mtime_ns: int, size: int, path: Path
) -> tuple[SourceFile, EmbeddedTags]:
    duration = 0.0
    try:
        audio = MutagenFile(path)
    except (MutagenError, OSError):
        audio = None
    if audio is not None:
        if audio.info is not None:
            duration = float(getattr(audio.info, "length", 0.0))
        tags = tags_from_loaded(audio, path)
    else:
        # MutagenFile couldn't identify an audio stream (e.g. a tag-only file with no
        # decodable audio). Tags may still be present, so read them via the direct
        # tag-container open so they aren't lost. Real library files always carry a stream,
        # so this fallback never fires on the hot path — the single load above stands.
        tags = read_embedded_tags(path)
    # mutagen returns 0 for files it can't sync to (e.g. an mp3 with a broken/absent VBR header) even
    # when the file has real audio. Fall back to ffprobe, which decodes the container/stream directly.
    # Only on the failure path (a nonempty file mutagen read as 0s), so the hot path is unaffected.
    if duration <= 0.0 and size > 0:
        try:
            duration = probe_duration_seconds(path)
            logger.info(f"duration: recovered {duration:.0f}s for {path} via ffprobe (mutagen read 0)")
        except (FFmpegError, OSError):
            # FFmpegError = ffprobe ran but found no duration (an empty/corrupt file); OSError =
            # ffprobe isn't installed. Either way we couldn't recover — leave duration 0.
            logger.warning(f"duration: no readable audio in {path} (mutagen and ffprobe both failed)")
    sf = SourceFile(
        path=path,
        size=size,
        duration_seconds=duration,
        ext=path.suffix.lower().lstrip("."),
    )
    return sf, tags


def clear_audio_metadata_cache() -> None:
    """Drop the in-memory audio-metadata cache (used by tests for determinism)."""
    _read_audio_metadata.cache_clear()


def probe_audio_file(path: Path) -> SourceFile:
    """Build a SourceFile for one audio file: size, duration, and bare extension.
    Thin wrapper over the cached `read_audio_metadata` (kept for non-scan callers)."""
    return read_audio_metadata(path)[0]
