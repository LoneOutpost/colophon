"""Read embedded tags from audio files via mutagen, dispatched by extension.

MP3 uses ID3 frames (custom TXXX frames for narrator/series/sequence/asin);
MP4/M4B uses atoms and `----` freeform atoms for the same custom fields.
"""

from __future__ import annotations

from pathlib import Path

from colophon.core.coerce import to_float, year_or_none
from colophon.core.models import EmbeddedTags


def read_embedded_tags(path: Path) -> EmbeddedTags:
    ext = path.suffix.lower()
    if ext == ".mp3":
        return _read_mp3(path)
    if ext in {".m4a", ".m4b", ".mp4", ".aac"}:
        return _read_mp4(path)
    return EmbeddedTags()


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    return None


def _read_mp3(path: Path) -> EmbeddedTags:
    from mutagen import MutagenError
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        tags = ID3(path)
    except (ID3NoHeaderError, MutagenError, OSError):
        return EmbeddedTags()

    def txxx(desc: str) -> str | None:
        frame = tags.get(f"TXXX:{desc}")
        return str(frame.text[0]) if frame and frame.text else None

    def frame_text(frame_id: str) -> str | None:
        frame = tags.get(frame_id)
        return str(frame.text[0]) if frame and frame.text else None

    def comm() -> str | None:
        # Mutagen keys COMM frames as "COMM:<desc>:<lang>", so a plain
        # tags.get("COMM") never matches; scan for the first COMM* frame.
        for key, frame in tags.items():
            if key.startswith("COMM") and getattr(frame, "text", None):
                return str(frame.text[0])
        return None

    return EmbeddedTags(
        title=frame_text("TIT2"),
        album=frame_text("TALB"),
        artist=frame_text("TPE1"),
        narrator=txxx("narrator"),
        series=txxx("series"),
        sequence=to_float(txxx("sequence")),
        year=year_or_none(frame_text("TDRC")),
        genre=frame_text("TCON"),
        description=comm(),
        asin=txxx("asin"),
    )


def _read_mp4(path: Path) -> EmbeddedTags:
    from mutagen import MutagenError
    from mutagen.mp4 import MP4

    try:
        m = MP4(path)
    except (MutagenError, OSError):
        return EmbeddedTags()

    def freeform(name: str) -> str | None:
        key = f"----:com.apple.iTunes:{name}"
        value = m.get(key)
        if value:
            raw = value[0]
            return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        return None

    return EmbeddedTags(
        title=_first(m.get("\xa9nam")),
        album=_first(m.get("\xa9alb")),
        artist=_first(m.get("\xa9ART")),
        narrator=freeform("narrator"),
        series=freeform("series"),
        sequence=to_float(freeform("sequence")),
        year=year_or_none(_first(m.get("\xa9day"))),
        genre=_first(m.get("\xa9gen")),
        description=_first(m.get("desc")) or _first(m.get("\xa9cmt")),
        asin=freeform("asin"),
    )
