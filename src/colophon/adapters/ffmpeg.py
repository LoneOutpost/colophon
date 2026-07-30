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

    `codec` is "copy" (remux) or "aac" (transcode at `bitrate`). Raises FFmpegError on failure.

    The transcode path uses ffmpeg's concat FILTER, which decodes and resamples each input
    independently before joining, so a book with heterogeneous inputs (mixed codecs / sample rates,
    e.g. mp3 + opus) concatenates correctly. The concat DEMUXER, by contrast, treats the list as one
    stream with the first input's codec and silently drops any segment that doesn't match — which
    truncated mixed-format books. The copy (remux) path stays on the demuxer: it is only chosen for a
    single, already-AAC input, which is uniform by construction.
    """
    for p in inputs:
        if not p.exists():
            raise FFmpegError(f"input does not exist: {p}")
    output.parent.mkdir(parents=True, exist_ok=True)

    if codec == "copy":
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listf:
            for p in inputs:
                escaped = str(p.resolve()).replace("'", "'\\''")
                listf.write(f"file '{escaped}'\n")
            list_path = Path(listf.name)
        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-i", str(metadata_path), "-map_metadata", "1", "-vn",
            "-c:a", "copy", "-f", "mp4", str(output),
        ]
        try:
            _run(args, timeout=timeout)
        finally:
            list_path.unlink(missing_ok=True)
        return

    # Transcode: concat filter over every input, then the metadata file as the last input so its
    # chapters map onto the output. The filter decodes/resamples each segment, so inputs with
    # different codecs, sample rates, or channel layouts join without dropping any of them.
    n = len(inputs)
    concat_filter = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p in inputs:
        args += ["-i", str(p)]
    args += [
        "-i", str(metadata_path),
        "-filter_complex", concat_filter, "-map", "[a]",
        "-map_metadata", str(n),
        "-c:a", "aac", "-b:a", bitrate,
        "-f", "mp4", str(output),
    ]
    _run(args, timeout=timeout)
