"""Chapter model, file-boundary chapter construction, and ffmetadata serialization."""

from __future__ import annotations

from colophon.core.filename_parser import strip_ext
from colophon.core.models import Chapter

__all__ = ["Chapter", "file_boundary_chapters", "to_ffmetadata"]


def file_boundary_chapters(files: list[tuple[str, float]]) -> list[Chapter]:
    """One chapter per source file. `files` is (filename, duration_seconds), in order.

    Titles are the filename without extension; the timeline accumulates in ms.
    """
    chapters: list[Chapter] = []
    cursor_ms = 0
    for name, duration_s in files:
        length_ms = round(duration_s * 1000)
        title = strip_ext(name)
        chapters.append(Chapter(title=title, start_ms=cursor_ms, end_ms=cursor_ms + length_ms))
        cursor_ms += length_ms
    return chapters


def _escape(value: str) -> str:
    # ffmetadata: escape =, ;, #, \ and newlines with a leading backslash.
    out = value.replace("\\", "\\\\")
    for ch in ("=", ";", "#"):
        out = out.replace(ch, f"\\{ch}")
    return out.replace("\n", "\\\n")


def to_ffmetadata(chapters: list[Chapter]) -> str:
    """Serialize chapters into an ffmpeg ffmetadata document."""
    lines = [";FFMETADATA1"]
    for ch in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={ch.start_ms}",
            f"END={ch.end_ms}",
            f"title={_escape(ch.title)}",
        ]
    return "\n".join(lines) + "\n"
