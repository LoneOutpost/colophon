"""Per-format audio handlers behind one interface: tag read/write, cover embedding, and audio-info
extraction. One handler per codec family; `tags.py` / `audio.py` delegate to the registry here so a
format's ID3-frame / MP4-atom / vorbis-comment knowledge lives in one place."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from colophon.core.coerce import to_float, year_or_none
from colophon.core.errors import TagWriteError
from colophon.core.models import EmbeddedTags


@dataclass(frozen=True)
class AudioInfo:
    bitrate: int
    sample_rate: int
    channels: int


def _first(value: object) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    return None


class AudioFormat:
    """Base handler. `exts` names the extensions a subclass owns. `read_info` is concrete here (the
    bitrate fallback is codec-agnostic); the tag/cover methods are per-format."""

    exts: frozenset[str] = frozenset()

    def read_tags(self, path: Path) -> EmbeddedTags:
        raise NotImplementedError

    def tags_from_loaded(self, audio) -> EmbeddedTags:
        raise NotImplementedError

    def write_tags(self, path: Path, tags: EmbeddedTags) -> None:
        raise NotImplementedError

    def embed_cover(self, path: Path, data: bytes, mime: str) -> None:
        raise NotImplementedError

    def read_info(self, audio, *, size: int, duration: float) -> AudioInfo:
        """bitrate/sample_rate/channels from the loaded container. When mutagen reports no bitrate
        (e.g. OggOpus), derive the effective average from size and duration so the file still counts
        as known-quality. Formats mutagen measures (mp3/mp4/flac) never hit the fallback."""
        bitrate = sample_rate = channels = 0
        info = getattr(audio, "info", None) if audio is not None else None
        if info is not None:
            bitrate = int(getattr(info, "bitrate", 0) or 0)
            sample_rate = int(getattr(info, "sample_rate", 0) or 0)
            channels = int(getattr(info, "channels", 0) or 0)
        if bitrate == 0 and size > 0 and duration > 0:
            bitrate = int(size * 8 / duration)
        return AudioInfo(bitrate=bitrate, sample_rate=sample_rate, channels=channels)


class Mp3Format(AudioFormat):
    exts = frozenset({".mp3"})

    def read_tags(self, path: Path) -> EmbeddedTags:
        from mutagen import MutagenError
        from mutagen.id3 import ID3, ID3NoHeaderError

        try:
            return self._from_id3(ID3(path))
        except (ID3NoHeaderError, MutagenError, OSError):
            return EmbeddedTags()

    def tags_from_loaded(self, audio) -> EmbeddedTags:
        id3 = getattr(audio, "tags", None)
        return self._from_id3(id3) if id3 is not None else EmbeddedTags()

    @staticmethod
    def _from_id3(tags) -> EmbeddedTags:
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

        trck = str(tags.get("TRCK", "")).split("/")[0].strip()
        track = int(trck) if trck.isdigit() else None

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
            track=track,
        )

    def write_tags(self, path: Path, tags: EmbeddedTags) -> None:
        from mutagen.id3 import (  # type: ignore[attr-defined]
            COMM,
            ID3,
            TALB,
            TCON,
            TDRC,
            TIT2,
            TPE1,
            TRCK,
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
        set_text(TRCK, tags.track)
        id3.save(path, v2_version=3)

    def embed_cover(self, path: Path, data: bytes, mime: str) -> None:
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError  # type: ignore[attr-defined]

        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        id3.save(path, v2_version=3)


class Mp4Format(AudioFormat):
    exts = frozenset({".m4a", ".m4b", ".mp4", ".aac"})

    def read_tags(self, path: Path) -> EmbeddedTags:
        from mutagen import MutagenError
        from mutagen.mp4 import MP4

        try:
            return self._from_mp4(MP4(path))
        except (MutagenError, OSError):
            return EmbeddedTags()

    def tags_from_loaded(self, audio) -> EmbeddedTags:
        return self._from_mp4(audio) if audio is not None else EmbeddedTags()

    @staticmethod
    def _from_mp4(m) -> EmbeddedTags:
        """Build EmbeddedTags from a loaded MP4 object (mutagen MP4 / MutagenFile for .m4*).
        Caller guarantees `m` is not None."""

        def freeform(name: str) -> str | None:
            key = f"----:com.apple.iTunes:{name}"
            value = m.get(key)
            if value:
                raw = value[0]
                return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            return None

        trkn = m.get("trkn") if m.tags else None
        track = trkn[0][0] if trkn and trkn[0] and trkn[0][0] else None

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
            track=track,
        )

    def write_tags(self, path: Path, tags: EmbeddedTags) -> None:
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
        m.pop("trkn", None)
        if tags.track is not None:
            m["trkn"] = [(tags.track, 0)]
        m.save()

    def embed_cover(self, path: Path, data: bytes, mime: str) -> None:
        from mutagen.mp4 import MP4, MP4Cover

        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        m = MP4(path)
        m["covr"] = [MP4Cover(data, imageformat=fmt)]
        m.save()


class VorbisFormat(AudioFormat):
    exts = frozenset({".opus", ".ogg", ".oga", ".flac"})

    def read_tags(self, path: Path) -> EmbeddedTags:
        import mutagen
        from mutagen import MutagenError

        try:
            return self.tags_from_loaded(mutagen.File(path))
        except (MutagenError, OSError):
            return EmbeddedTags()

    def tags_from_loaded(self, audio) -> EmbeddedTags:
        """Build EmbeddedTags from a loaded vorbis-comment container (OggOpus/OggVorbis/FLAC), which
        behaves as a case-insensitive str -> list[str] mapping. `audio` None (unreadable) -> empty tags."""
        if audio is None:
            return EmbeddedTags()

        def get(key: str) -> str | None:
            return _first(audio.get(key))

        trk = (get("TRACKNUMBER") or "").split("/")[0].strip()
        track = int(trk) if trk.isdigit() else None
        return EmbeddedTags(
            title=get("TITLE"),
            album=get("ALBUM"),
            artist=get("ARTIST"),
            narrator=get("NARRATOR"),
            series=get("SERIES"),
            sequence=to_float(get("SERIES-PART")),
            year=year_or_none(get("DATE")),
            genre=get("GENRE"),
            description=get("DESCRIPTION"),
            asin=get("ASIN"),
            isbn=get("ISBN"),
            track=track,
        )

    def write_tags(self, path: Path, tags: EmbeddedTags) -> None:
        import mutagen

        audio = mutagen.File(path)
        if audio is None:
            raise TagWriteError(f"unreadable audio container: {path}")
        if audio.tags is None:
            audio.add_tags()

        def put(key: str, value: object) -> None:
            audio.pop(key, None)          # clear first so a None value removes the field
            if value is not None:
                audio[key] = [str(value)]

        put("TITLE", tags.title)
        put("ALBUM", tags.album)
        put("ARTIST", tags.artist)
        put("DATE", tags.year)
        put("GENRE", tags.genre)
        put("DESCRIPTION", tags.description)
        put("NARRATOR", tags.narrator)
        put("SERIES", tags.series)
        put("SERIES-PART", tags.sequence)
        put("ASIN", tags.asin)
        put("ISBN", tags.isbn)
        put("TRACKNUMBER", tags.track)
        audio.save()

    def embed_cover(self, path: Path, data: bytes, mime: str) -> None:
        import base64

        from mutagen.flac import FLAC, Picture

        pic = Picture()
        pic.type = 3          # front cover
        pic.mime = mime
        pic.data = data
        ext = path.suffix.lower()
        if ext == ".flac":
            flac = FLAC(path)
            flac.clear_pictures()
            flac.add_picture(pic)
            flac.save()
        else:                 # ogg / opus: base64 METADATA_BLOCK_PICTURE comment
            import mutagen

            audio = mutagen.File(path)
            if audio is None:
                raise TagWriteError(f"unreadable audio container: {path}")
            if audio.tags is None:
                audio.add_tags()
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            audio.save()


_FORMATS: tuple[AudioFormat, ...] = (Mp3Format(), Mp4Format(), VorbisFormat())
_BY_EXT: dict[str, AudioFormat] = {e: f for f in _FORMATS for e in f.exts}


def format_for(ext: str) -> AudioFormat | None:
    """The handler for a file extension (with or without the leading dot), or None if unsupported."""
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    return _BY_EXT.get(ext)
