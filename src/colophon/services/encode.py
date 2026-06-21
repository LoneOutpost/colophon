"""Encode a BookUnit's source files into a verified, chaptered M4B."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from colophon.adapters.ffmpeg import (
    FFmpegError,
    concat_encode,
    probe_codec,
    probe_duration_seconds,
)
from colophon.core.chapters import Chapter, file_boundary_chapters, to_ffmetadata
from colophon.core.models import BookUnit, _Base
from colophon.services.tag_ops import write_output_metadata

logger = logging.getLogger(__name__)

# Output duration must be within this tolerance of the summed inputs to verify.
_TOLERANCE_S = 2.0


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
) -> EncodeResult:
    """Build one chaptered M4B at `output_path` from `book.source_files`, verify it,
    and (only if verified AND delete_sources AND confirm_delete) delete the originals.

    `chapters`, if given, overrides the default file-boundary chapters (the seam for
    caller-supplied chapters, e.g. from Audnexus).
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

    try:
        concat_encode(inputs, output_path, metadata_path=meta_path, codec=codec, bitrate=bitrate)
        actual_s = probe_duration_seconds(output_path)
    except FFmpegError as e:
        logger.warning(f"encode failed for {book.id}: {e}")
        return EncodeResult(book_id=book.id, error=str(e))
    finally:
        meta_path.unlink(missing_ok=True)

    verified = abs(actual_s - expected_s) <= max(_TOLERANCE_S, 0.05 * expected_s)
    if not verified:
        output_path.unlink(missing_ok=True)
        return EncodeResult(
            book_id=book.id,
            output_path=None,
            verified=False,
            error=f"duration mismatch: expected ~{expected_s:.1f}s, got {actual_s:.1f}s",
        )

    write_output_metadata(book, output_path)

    deleted = False
    if delete_sources and confirm_delete:
        for p in inputs:
            p.unlink(missing_ok=True)
        deleted = True

    return EncodeResult(book_id=book.id, output_path=output_path, verified=True, deleted_sources=deleted)


def encode_batch(
    books: list[BookUnit],
    output_for: Callable[[BookUnit], Path],
    *,
    bitrate: str,
    max_workers: int = 2,
    delete_sources: bool = False,
    confirm_delete: bool = False,
) -> list[EncodeResult]:
    """Encode `books` concurrently. One failure never aborts the rest; every book
    yields an EncodeResult. Order of results matches `books`.
    """
    def _one(book: BookUnit) -> EncodeResult:
        try:
            return encode_book(
                book, output_for(book), bitrate=bitrate,
                delete_sources=delete_sources, confirm_delete=confirm_delete,
            )
        except Exception as e:  # never let one book sink the batch
            logger.warning(f"unexpected encode error for {book.id}: {e}")
            return EncodeResult(book_id=book.id, error=str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_one, books))
