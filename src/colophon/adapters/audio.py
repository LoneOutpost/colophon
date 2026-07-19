"""Filesystem-level audio inspection: extensions and per-file probing."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import MutagenError

from colophon.adapters.ffmpeg import FFmpegError, probe_duration_seconds
from colophon.adapters.tags import read_embedded_tags, tags_from_loaded
from colophon.core.audio_quality import codec_label
from colophon.core.models import EmbeddedTags, SourceFile

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".flac"}  # .mp4 is a video container

AUDIO_META_CACHE_SIZE = 4096  # bounds memory; within a scan the 3 reads of a file are
# consecutive (one book's processing), so the dedup win does not need a large cache — this
# size also lets re-scans of recently-seen files skip the parse.


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def read_audio_metadata(path: Path) -> tuple[SourceFile, EmbeddedTags]:
    """Open `path` exactly once and return both its SourceFile (size, duration, bare ext,
    and quality: bitrate/sample_rate/channels/codec) and its EmbeddedTags. Memoized on
    (path, st_mtime_ns, st_size): an unchanged file is
    served from memory; a changed file (including one a tag-write just touched) is re-read.

    The returned value objects are treated as immutable (no caller mutates SourceFile /
    EmbeddedTags fields — verified across the codebase), so the cached instances are shared.
    A future caller needing to mutate one must `.model_copy()` first.
    """
    st = path.stat()
    return _read_audio_metadata(str(path), st.st_mtime_ns, st.st_size, path)


_HEADER_PEEK = 256 * 1024  # a real mp3's ID3 tag + first frames sit well within this


def _has_data(path: Path) -> bool:
    """True if the file's leading bytes contain any non-zero data. A zero-filled placeholder (an
    incomplete download) has none — and ffprobe couldn't find audio there anyway — so this cheap
    read avoids an expensive multi-MB ffprobe probe on a file that has nothing to recover."""
    try:
        with open(path, "rb") as fh:
            return any(fh.read(_HEADER_PEEK))
    except OSError:
        return False


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
    # mutagen returns 0 for files it can't sync to (a broken/absent VBR header) even when the file
    # has real audio. Only then do we consider ffprobe, which decodes the stream directly. But
    # ffprobe reads several MB hunting for a frame, which is wasted on a zero-filled placeholder (an
    # incomplete download) — the common cause of a 0-length nonempty file. So gate it on a cheap
    # header peek: no data in the header means no audio to find, skip straight to 0.
    if duration <= 0.0 and size > 0:
        if not _has_data(path):
            logger.warning(f"duration: {path} is empty/zero-filled (incomplete download); no audio")
        else:
            try:
                duration = probe_duration_seconds(path)
                logger.info(f"duration: recovered {duration:.0f}s for {path} via ffprobe (mutagen 0)")
            except (FFmpegError, OSError):
                # ffprobe found no duration (corrupt) or isn't installed — leave duration 0.
                logger.warning(f"duration: no readable audio in {path} (mutagen and ffprobe failed)")
    bitrate = sample_rate = channels = 0
    if audio is not None and audio.info is not None:
        bitrate = int(getattr(audio.info, "bitrate", 0) or 0)
        sample_rate = int(getattr(audio.info, "sample_rate", 0) or 0)
        channels = int(getattr(audio.info, "channels", 0) or 0)
    ext = path.suffix.lower().lstrip(".")
    sf = SourceFile(
        path=path,
        size=size,
        duration_seconds=duration,
        ext=ext,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec_label(ext),
    )
    return sf, tags


def clear_audio_metadata_cache() -> None:
    """Drop the in-memory audio-metadata cache (used by tests for determinism)."""
    _read_audio_metadata.cache_clear()


def probe_audio_file(path: Path) -> SourceFile:
    """Build a SourceFile for one audio file: size, duration, bare extension, and quality
    (bitrate/sample_rate/channels/codec). Thin wrapper over the cached `read_audio_metadata`
    (kept for non-scan callers)."""
    return read_audio_metadata(path)[0]
