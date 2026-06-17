"""Download a cover image over HTTP and normalize its mime type.

Returns None (and logs a warning) on any HTTP failure, so a missing or
unreachable cover degrades gracefully and never aborts a tag write. The mime is
normalized to exactly 'image/png' or 'image/jpeg' — the two values embed_cover
accepts — from the response Content-Type, falling back to the URL extension.
"""

from __future__ import annotations

import logging

import httpx

from colophon.core.models import _Base

logger = logging.getLogger(__name__)


class CoverImage(_Base):
    data: bytes
    mime: str


def _normalize_mime(content_type: str | None, url: str) -> str:
    value = (content_type or "").split(";")[0].strip().lower()
    if value == "image/png":
        return "image/png"
    if value in {"image/jpeg", "image/jpg"}:
        return "image/jpeg"
    return "image/png" if url.lower().rsplit("?", 1)[0].endswith(".png") else "image/jpeg"


async def fetch_cover(url: str, *, client: httpx.AsyncClient | None = None) -> CoverImage | None:
    """Download `url` and return its bytes + normalized mime, or None on failure."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return CoverImage(data=resp.content, mime=_normalize_mime(resp.headers.get("content-type"), url))
    except httpx.HTTPError as e:
        logger.warning(f"cover download failed for {url}: {e}")
        return None
    finally:
        if own_client:
            await client.aclose()
