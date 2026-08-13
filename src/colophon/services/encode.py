"""Encode a BookUnit's source files into a verified, chaptered M4B."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from colophon.adapters.ffmpeg import (
    FFmpegError,
    concat_encode,
    probe_codec,
    probe_duration_seconds,
)
from colophon.core.chapters import Chapter, file_boundary_chapters, to_ffmetadata
from colophon.core.models import BookUnit, _Base

logger = logging.getLogger(__name__)
# Live encode progress rides the always-on `colophon.progress` logger (same channel as `step`).
progress_logger = logging.getLogger("colophon.progress")

# Output duration must be within this tolerance of the summed inputs to verify.
_TOLERANCE_S = 2.0
# Log an encode-progress line each time completion advances by this many percent.
_PCT_STEP = 5


class EncodeResult(_Base):
    book_id: str
    output_path: Path | None = None
    verified: bool = False
    deleted_sources: bool = False
    error: str | None = None


def _choose_codec(book: BookUnit) -> str:
    """Remux a single already-AAC input; otherwise transcode to AAC."""
    files = book.source_files
    if len(files) == 1:
        try:
            if probe_codec(files[0].path) == "aac":
                return "copy"
        except FFmpegError:
            return "aac"
    return "aac"


def encode_book(
    book: BookUnit,
    output_path: Path,
    *,
    bitrate: str,
    delete_sources: bool = False,
    confirm_delete: bool = False,
    chapters: list[Chapter] | None = None,
    progress: Callable[[float], None] | None = None,
) -> EncodeResult:
    """Build one chaptered M4B at `output_path` from `book.source_files`, verify it,
    and (only if verified AND delete_sources AND confirm_delete) delete the originals.

    `chapters`, if given, overrides the default file-boundary chapters (the seam for
    caller-supplied chapters, e.g. from Audnexus).

    Encode progress is reported two ways as ffmpeg runs: a completion fraction in [0, 1] to the
    optional `progress` callback (for the UI), and throttled percentage lines on the `colophon.progress`
    logger (so a long transcode is no longer silent in the log).
    """
    if not book.source_files:
        return EncodeResult(book_id=book.id, error="no source files")

    inputs = [sf.path for sf in book.source_files]
    expected_s = sum(sf.duration_seconds for sf in book.source_files)
    if chapters is None:
        chapters = file_boundary_chapters(
            [(sf.path.name, sf.duration_seconds) for sf in book.source_files]
        )
    codec = _choose_codec(book)

    with tempfile.NamedTemporaryFile("w", suffix=".ffmeta", delete=False) as mf:
        mf.write(to_ffmetadata(chapters))
        meta_path = Path(mf.name)

    label = book.title or book.id
    t0 = perf_counter()
    logged_pct = -_PCT_STEP   # force a log at the first tick
    progress_logger.info(
        f"encode {label!r}: starting ({len(inputs)} file(s), ~{expected_s / 60:.0f} min)"
    )

    def _on_progress(frac: float) -> None:
        nonlocal logged_pct
        if progress is not None:
            progress(frac)
        pct = int(frac * 100)
        if pct >= logged_pct + _PCT_STEP:
            logged_pct = pct
            progress_logger.info(f"encode {label!r}: {pct}% ({perf_counter() - t0:.0f}s)")

    try:
        concat_encode(inputs, output_path, metadata_path=meta_path, codec=codec, bitrate=bitrate,
                      total_seconds=expected_s, on_progress=_on_progress)
        actual_s = probe_duration_seconds(output_path)
    except FFmpegError as e:
        logger.warning(f"encode failed for {book.id}: {e}")
        progress_logger.warning(f"encode {label!r}: failed after {perf_counter() - t0:.0f}s: {e}")
        return EncodeResult(book_id=book.id, error=str(e))
    finally:
        meta_path.unlink(missing_ok=True)
    progress_logger.info(f"encode {label!r}: done in {perf_counter() - t0:.0f}s")

    verified = abs(actual_s - expected_s) <= max(_TOLERANCE_S, 0.05 * expected_s)
    if not verified:
        output_path.unlink(missing_ok=True)
        return EncodeResult(
            book_id=book.id,
            output_path=None,
            verified=False,
            error=f"duration mismatch: expected ~{expected_s:.1f}s, got {actual_s:.1f}s",
        )

    deleted = False
    if delete_sources and confirm_delete:
        for p in inputs:
            p.unlink(missing_ok=True)
        deleted = True

    return EncodeResult(book_id=book.id, output_path=output_path, verified=True, deleted_sources=deleted)
