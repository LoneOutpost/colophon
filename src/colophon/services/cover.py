"""Persist a book's cover image to disk and record it on the BookUnit.

`ensure_cached_cover` downloads the book's `cover_url` (if any) via the cover
adapter, writes it next to the book as `cover.<ext>`, and sets `cover_path`.
Returns the cached path, or None when there is no URL, the download fails, or
the cache write fails — every failure mode is non-fatal so a missing cover never
aborts a tag write.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from colophon.adapters.cover import ext_for_mime, fetch_cover
from colophon.core.models import BookUnit

logger = logging.getLogger(__name__)

THUMB_MAX_PX = 96  # longest edge of the list/navigator thumbnail


def _thumb_path(source: Path) -> Path:
    # Beside the source so it travels with the cover; the full source name keeps
    # two covers of different types (cover.jpg / cover.png) from colliding.
    return source.with_name(source.name + ".thumb.jpg")


def thumbnail_bytes(source: Path, *, max_px: int = THUMB_MAX_PX) -> tuple[bytes, str] | None:
    """A small JPEG thumbnail of `source` as (bytes, "image/jpeg"), generated and
    cached beside it on first use and regenerated when the source is newer.

    Returns None when the source is missing or not a decodable image, so the
    caller can fall back to serving the full-size cover. Synchronous (Pillow):
    call it from a worker thread when on the event loop.
    """
    if not source.exists():
        return None
    thumb = _thumb_path(source)
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            with Image.open(source) as im:
                rgb = im.convert("RGB")
                rgb.thumbnail((max_px, max_px))
                rgb.save(thumb, "JPEG", quality=82)
        except (OSError, UnidentifiedImageError, ValueError) as e:
            logger.warning(f"thumbnailing {source} failed: {e}")
            return None
    return thumb.read_bytes(), "image/jpeg"


async def ensure_cached_cover(
    book: BookUnit, *, dest_dir: Path, client: httpx.AsyncClient | None = None
) -> Path | None:
    if not book.cover_url:
        return None
    cover = await fetch_cover(book.cover_url, client=client)
    if cover is None:
        return None
    path = dest_dir / f"cover{ext_for_mime(cover.mime)}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cover.data)
    except OSError as e:  # disk full / read-only / bad path — degrade like a failed download
        logger.warning(f"caching cover to {path} failed: {e}")
        return None
    book.cover_path = path
    return path
