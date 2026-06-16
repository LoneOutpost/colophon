"""Filesystem-level audio inspection: extensions and per-file probing."""

from __future__ import annotations

from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import MutagenError

from colophon.core.models import SourceFile

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".flac"}


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def probe_audio_file(path: Path) -> SourceFile:
    """Build a SourceFile for one audio file: size, duration, and bare extension."""
    size = path.stat().st_size
    duration = 0.0
    try:
        audio = MutagenFile(path)
    except (MutagenError, OSError):
        audio = None
    if audio is not None and audio.info is not None:
        duration = float(getattr(audio.info, "length", 0.0))
    return SourceFile(
        path=path,
        size=size,
        duration_seconds=duration,
        ext=path.suffix.lower().lstrip("."),
    )
