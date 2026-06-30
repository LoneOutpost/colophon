"""Read embedded tags from audio files via mutagen, dispatched by extension.

MP3 uses ID3 frames (custom TXXX frames for narrator/series/sequence/asin);
MP4/M4B uses atoms and `----` freeform atoms for the same custom fields.
"""

from __future__ import annotations

from pathlib import Path

from colophon.core.coerce import to_float, year_or_none
from colophon.core.errors import TagWriteError
from colophon.core.models import EmbeddedTags

# Extension dispatch, shared by the read and write paths so the two cannot drift
# (round-trip parity depends on read and write routing a file to the same codec).
_MP3_EXT = ".mp3"
_MP4_EXTS = {".m4a", ".m4b", ".mp4", ".aac"}


def read_embedded_tags(path: Path) -> EmbeddedTags:
    """Open `path` and extract its embedded tags (used by non-scan callers and as the
    parity oracle for `tags_from_loaded`). Opens the tag container directly, so it works
    even for a frames-only file with no decodable audio stream."""
    from mutagen import MutagenError

    ext = path.suffix.lower()
    if ext == _MP3_EXT:
        from mutagen.id3 import ID3, ID3NoHeaderError

        try:
            return _tags_from_id3(ID3(path))
        except (ID3NoHeaderError, MutagenError, OSError):
            return EmbeddedTags()
    if ext in _MP4_EXTS:
        from mutagen.mp4 import MP4

        try:
            return _tags_from_mp4(MP4(path))
        except (MutagenError, OSError):
            return EmbeddedTags()
    return EmbeddedTags()


def tags_from_loaded(audio, path: Path) -> EmbeddedTags:
    """Extract EmbeddedTags from an object already loaded by `mutagen.File(path)`,
    dispatched by extension (same routing the write path uses, so read/write cannot drift).
    MP3: frames live on `audio.tags` (an ID3 object, or None when the file has no ID3
    header). MP4: the loaded object IS the tag mapping. None / unsupported extension ->
    empty tags."""
    ext = path.suffix.lower()
    if ext == _MP3_EXT:
        id3 = getattr(audio, "tags", None)
        return _tags_from_id3(id3) if id3 is not None else EmbeddedTags()
    if ext in _MP4_EXTS:
        return _tags_from_mp4(audio) if audio is not None else EmbeddedTags()
    return EmbeddedTags()


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    return None


def _tags_from_id3(tags) -> EmbeddedTags:
    """Build EmbeddedTags from a loaded ID3 frames object (mutagen ID3 / MP3.tags).
    Caller guarantees `tags` is not None."""

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
        isbn=txxx("isbn"),
    )


def _tags_from_mp4(m) -> EmbeddedTags:
    """Build EmbeddedTags from a loaded MP4 object (mutagen MP4 / MutagenFile for .m4*).
    Caller guarantees `m` is not None."""

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
        isbn=freeform("isbn"),
    )


def write_embedded_tags(path: Path, tags: EmbeddedTags) -> None:
    """Write `tags` into the audio file at `path`, dispatched by extension.

    Uses the same ID3 frame / MP4 atom keys the read side reads, so a written
    file reads back as the same EmbeddedTags. The managed fields mirror `tags`
    exactly: a non-None value is set and a None value clears that field, so the
    file's managed tags always equal `tags` (this makes writes idempotent and
    lets a prior snapshot be restored faithfully on revert). Tags outside the
    managed set are left intact. Raises TagWriteError on an unsupported format or
    a mutagen failure.
    """
    ext = path.suffix.lower()
    try:
        if ext == _MP3_EXT:
            _write_mp3(path, tags)
        elif ext in _MP4_EXTS:
            _write_mp4(path, tags)
        else:
            raise TagWriteError(f"unsupported audio format for writing: {ext}")
    except TagWriteError:
        raise
    except Exception as e:  # mutagen/OS failure -> typed domain error
        raise TagWriteError(f"write tags to {path} failed: {e}") from e


def _write_mp3(path: Path, tags: EmbeddedTags) -> None:
    from mutagen.id3 import (  # type: ignore[attr-defined]
        COMM,
        ID3,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TXXX,
        ID3NoHeaderError,
    )

    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()

    def set_text(frame_cls, value: object) -> None:
        id3.delall(frame_cls.__name__)  # clear first so a None value removes the frame
        if value is None:
            return
        id3.add(frame_cls(encoding=3, text=str(value)))

    def set_txxx(desc: str, value: object) -> None:
        id3.delall(f"TXXX:{desc}")
        if value is None:
            return
        id3.add(TXXX(encoding=3, desc=desc, text=str(value)))

    set_text(TIT2, tags.title)
    set_text(TALB, tags.album)
    set_text(TPE1, tags.artist)
    set_text(TDRC, tags.year)
    set_text(TCON, tags.genre)
    id3.delall("COMM")  # clear first so a None description removes the comment frame
    if tags.description is not None:
        id3.add(COMM(encoding=3, lang="eng", desc="", text=str(tags.description)))
    set_txxx("narrator", tags.narrator)
    set_txxx("series", tags.series)
    set_txxx("sequence", tags.sequence)
    set_txxx("asin", tags.asin)
    set_txxx("isbn", tags.isbn)
    id3.save(path, v2_version=3)


def _write_mp4(path: Path, tags: EmbeddedTags) -> None:
    from mutagen.mp4 import MP4, MP4FreeForm

    m = MP4(path)

    def set_atom(key: str, value: object) -> None:
        m.pop(key, None)  # clear first so a None value removes the atom
        if value is None:
            return
        m[key] = [str(value)]

    def set_freeform(name: str, value: object) -> None:
        key = f"----:com.apple.iTunes:{name}"
        m.pop(key, None)
        if value is None:
            return
        m[key] = [MP4FreeForm(str(value).encode("utf-8"))]

    set_atom("\xa9nam", tags.title)
    set_atom("\xa9alb", tags.album)
    set_atom("\xa9ART", tags.artist)
    set_atom("\xa9day", tags.year)
    set_atom("\xa9gen", tags.genre)
    set_atom("desc", tags.description)
    m.pop("\xa9cmt", None)  # clear the legacy comment atom the reader falls back to, so desc is authoritative
    set_freeform("narrator", tags.narrator)
    set_freeform("series", tags.series)
    set_freeform("sequence", tags.sequence)
    set_freeform("asin", tags.asin)
    set_freeform("isbn", tags.isbn)
    m.save()


def embed_cover(path: Path, image_bytes: bytes, mime: str) -> None:
    """Embed cover art into the audio file at `path`. mime is 'image/png' or
    'image/jpeg'. Replaces any existing cover. Raises TagWriteError on an
    unsupported format or a mutagen failure."""
    ext = path.suffix.lower()
    try:
        if ext == _MP3_EXT:
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError  # type: ignore[attr-defined]

            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
            id3.save(path, v2_version=3)
        elif ext in _MP4_EXTS:
            from mutagen.mp4 import MP4, MP4Cover

            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            m = MP4(path)
            m["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
            m.save()
        else:
            raise TagWriteError(f"unsupported audio format for cover: {ext}")
    except TagWriteError:
        raise
    except Exception as e:
        raise TagWriteError(f"embed cover into {path} failed: {e}") from e
