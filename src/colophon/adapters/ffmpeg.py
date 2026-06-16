"""Thin wrappers over the ffmpeg/ffprobe subprocesses."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


class FFmpegError(RuntimeError):
    """An ffmpeg/ffprobe invocation failed."""


def _run(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"{args[0]} timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise FFmpegError(f"{args[0]} failed ({proc.returncode}): {proc.stderr[:500]}")
    return proc


def probe_duration_seconds(path: Path) -> float:
    proc = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ], timeout=60)
    data = json.loads(proc.stdout or "{}")
    try:
        return float(data["format"]["duration"])
    except (KeyError, ValueError, TypeError) as e:
        raise FFmpegError(f"no duration for {path}") from e


def probe_codec(path: Path) -> str:
    proc = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "json", str(path),
    ], timeout=60)
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise FFmpegError(f"no audio stream in {path}")
    return str(streams[0].get("codec_name", ""))


def probe_chapter_count(path: Path) -> int:
    proc = _run([
        "ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path),
    ], timeout=60)
    data = json.loads(proc.stdout or "{}")
    return len(data.get("chapters") or [])


def concat_encode(
    inputs: list[Path],
    output: Path,
    *,
    metadata_path: Path,
    codec: str,
    bitrate: str,
    timeout: float = 3600,
) -> None:
    """Concatenate `inputs` into a single M4B at `output` with chapters from metadata.

    `codec` is "copy" (remux) or "aac" (transcode at `bitrate`). Raises FFmpegError
    on failure. Uses ffmpeg's concat demuxer via a temporary list file.
    """
    for p in inputs:
        if not p.exists():
            raise FFmpegError(f"input does not exist: {p}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listf:
        for p in inputs:
            escaped = str(p.resolve()).replace("'", "'\\''")
            listf.write(f"file '{escaped}'\n")
        list_path = Path(listf.name)

    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(metadata_path), "-map_metadata", "1",
        "-vn",
    ]
    if codec == "copy":
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", bitrate]
    args += ["-f", "mp4", str(output)]

    try:
        _run(args, timeout=timeout)
    finally:
        list_path.unlink(missing_ok=True)
