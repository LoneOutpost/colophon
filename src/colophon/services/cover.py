"""Persist a book's cover image to disk and record it on the BookUnit.

`ensure_cached_cover` downloads the book's `cover_url` (if any) via the cover
adapter, writes it next to the book as `cover.<ext>`, and sets `cover_path`.
Returns the cached path, or None when there is no URL or the download fails —
the caller treats a missing cover as a non-fatal, skippable step.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from colophon.adapters.cover import fetch_cover
from colophon.core.models import BookUnit


async def ensure_cached_cover(
    book: BookUnit, *, dest_dir: Path, client: httpx.AsyncClient | None = None
) -> Path | None:
    if not book.cover_url:
        return None
    cover = await fetch_cover(book.cover_url, client=client)
    if cover is None:
        return None
    ext = ".png" if cover.mime == "image/png" else ".jpg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"cover{ext}"
    path.write_bytes(cover.data)
    book.cover_path = path
    return path
