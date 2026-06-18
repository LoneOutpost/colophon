"""Stream an HTTP URL to a local file, atomically (temp .part then rename)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_CHUNK = 1 << 16


async def stream_download(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Download `url` to `dest`. Writes to `dest` + ".part" and renames on success
    so a partial transfer never leaves a usable file at `dest`. `progress(done,
    total)` is called as bytes arrive (`total` is 0 when the server omits a
    Content-Length). Raises httpx errors on failure (leaving only the .part).

    When `client` is injected the caller owns its lifecycle; otherwise a private
    client is created and closed here."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        tmp.replace(dest)
    finally:
        if owns:
            await client.aclose()
