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

from colophon.adapters.cover import ext_for_mime, fetch_cover
from colophon.core.models import BookUnit

logger = logging.getLogger(__name__)


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
