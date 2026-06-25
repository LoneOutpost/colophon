"""Chapter model, file-boundary chapter construction, and ffmetadata serialization."""

from __future__ import annotations

from colophon.core.filename_parser import strip_ext
from colophon.core.models import Chapter

__all__ = [
    "RUNTIME_MISMATCH_MS",
    "Chapter",
    "file_boundary_chapters",
    "format_timecode",
    "normalize_chapters",
    "parse_timecode",
    "runtime_mismatch",
    "shift_chapters",
    "to_ffmetadata",
]

# Tolerance before a fetched (e.g. Audible) runtime is flagged as not matching the
# summed source-file duration; chapter offsets drifting by more than this are suspect.
RUNTIME_MISMATCH_MS = 60_000


def runtime_mismatch(source_ms: int, candidate_ms: int) -> bool:
    """Whether two runtimes differ by more than RUNTIME_MISMATCH_MS."""
    return abs(candidate_ms - source_ms) > RUNTIME_MISMATCH_MS


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


def format_timecode(ms: int) -> str:
    """Milliseconds to 'H:MM:SS' (the form the chapter editor shows and accepts)."""
    total = max(0, ms) // 1000
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_timecode(text: str) -> int:
    """Parse 'H:MM:SS', 'MM:SS', or 'SS' into milliseconds. Raises ValueError on
    anything non-numeric or negative."""
    parts = text.strip().split(":")
    if not parts or len(parts) > 3:
        raise ValueError(f"bad timecode: {text!r}")
    try:
        nums = [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"bad timecode: {text!r}") from exc
    if any(n < 0 for n in nums):
        raise ValueError(f"bad timecode: {text!r}")
    seconds = 0
    for n in nums:  # left-to-right, each place is x60 of the next
        seconds = seconds * 60 + n
    return seconds * 1000


def normalize_chapters(chapters: list[Chapter], total_ms: int) -> list[Chapter]:
    """Sort chapters by start, clamp starts into [0, total_ms], and recompute each
    end to the next chapter's start (the last runs to total_ms). Titles are kept."""
    ordered = sorted(
        (Chapter(title=c.title, start_ms=min(max(c.start_ms, 0), total_ms), end_ms=c.end_ms)
         for c in chapters),
        key=lambda c: c.start_ms,
    )
    out: list[Chapter] = []
    for i, ch in enumerate(ordered):
        end = ordered[i + 1].start_ms if i + 1 < len(ordered) else total_ms
        out.append(Chapter(title=ch.title, start_ms=ch.start_ms, end_ms=max(end, ch.start_ms)))
    return out


def shift_chapters(chapters: list[Chapter], delta_ms: int, total_ms: int) -> list[Chapter]:
    """Shift every chapter start by `delta_ms` (clamped at 0), then normalize."""
    shifted = [
        Chapter(title=c.title, start_ms=c.start_ms + delta_ms, end_ms=c.end_ms)
        for c in chapters
    ]
    return normalize_chapters(shifted, total_ms)


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
