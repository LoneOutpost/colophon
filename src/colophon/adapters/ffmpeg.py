"""Thin wrappers over the ffmpeg/ffprobe subprocesses."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable
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


_OUT_TIME_TS = re.compile(r"^out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _parse_progress_us(line: str) -> int | None:
    """Microseconds elapsed from one ffmpeg `-progress` line, or None if the line carries no time.
    Prefers the unambiguous `out_time_us=` field and falls back to the `out_time=HH:MM:SS.ff`
    timestamp for ffmpeg builds that omit it. (The `out_time_ms=` field is deliberately ignored —
    it reports microseconds on some builds and milliseconds on others.)"""
    line = line.strip()
    if line.startswith("out_time_us="):
        try:
            return max(0, int(line[len("out_time_us="):]))
        except ValueError:
            return None
    m = _OUT_TIME_TS.match(line)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return int((h * 3600 + mn * 60 + s) * 1_000_000)
    return None


def _run_with_progress(
    args: list[str], *, timeout: float | None, total_seconds: float | None,
    on_progress: Callable[[float], None],
) -> None:
    """Run ffmpeg streaming its `-progress pipe:1` output, reporting completion in [0, 1] to
    `on_progress` as it advances (fraction = elapsed output time / `total_seconds`). stderr is
    drained on a thread so a chatty encoder can never deadlock on a full pipe. Raises FFmpegError on a
    non-zero exit or timeout, mirroring `_run`."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err: list[str] = []

    def _drain() -> None:
        if proc.stderr is not None:
            err.extend(proc.stderr)

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                us = _parse_progress_us(line)
                if us is not None and total_seconds and total_seconds > 0:
                    on_progress(max(0.0, min(1.0, (us / 1_000_000) / total_seconds)))
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        proc.wait()
        raise FFmpegError(f"{args[0]} timed out after {timeout}s") from e
    finally:
        drainer.join(timeout=1)
    if proc.returncode != 0:
        raise FFmpegError(f"{args[0]} failed ({proc.returncode}): {''.join(err)[:500]}")


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
    total_seconds: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Concatenate `inputs` into a single M4B at `output` with chapters from metadata.

    `codec` is "copy" (remux) or "aac" (transcode at `bitrate`). Raises FFmpegError on failure.

    The transcode path uses ffmpeg's concat FILTER, which decodes and resamples each input
    independently before joining, so a book with heterogeneous inputs (mixed codecs / sample rates,
    e.g. mp3 + opus) concatenates correctly. The concat DEMUXER, by contrast, treats the list as one
    stream with the first input's codec and silently drops any segment that doesn't match — which
    truncated mixed-format books. The copy (remux) path stays on the demuxer: it is only chosen for a
    single, already-AAC input, which is uniform by construction.

    When `on_progress` is given, ffmpeg streams its completion (fraction in [0, 1], measured against
    `total_seconds`) as the encode runs, so callers can surface live progress instead of a black box.
    """
    for p in inputs:
        if not p.exists():
            raise FFmpegError(f"input does not exist: {p}")
    output.parent.mkdir(parents=True, exist_ok=True)

    def _exec(args: list[str]) -> None:
        # Stream progress only when a sink is wired; otherwise keep the simple capture path.
        if on_progress is not None:
            args = [args[0], "-progress", "pipe:1", "-nostats", *args[1:]]
            _run_with_progress(args, timeout=timeout, total_seconds=total_seconds,
                               on_progress=on_progress)
        else:
            _run(args, timeout=timeout)

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
            _exec(args)
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
    _exec(args)
