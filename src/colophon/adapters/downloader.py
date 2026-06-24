"""Stream an HTTP URL to a local file, atomically (temp .part then rename),
with optional resume (HTTP Range) and cooperative cancel."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx

from colophon.core.cancel import CancelToken

logger = logging.getLogger(__name__)

_CHUNK = 1 << 16


class DownloadCancelled(Exception):
    """Raised when a download is cooperatively cancelled; the `.part` is retained
    so a later call resumes it."""


async def stream_download(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Download `url` to `dest` (writing `dest`+".part", renaming on success).

    Resume: if a `.part` already exists, request `Range: bytes=<size>-` and append;
    on a `206` the transfer resumes, on a `200` (server ignored the range) it
    restarts from zero. Cancel: when `cancel` is set the stream stops and the
    `.part` is left in place, raising `DownloadCancelled`. `progress(done, total)`
    reports cumulative bytes (`total` is 0 when no length is known)."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    tmp = dest.with_name(dest.name + ".part")
    resume_from = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else {}
    try:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416:  # range past EOF: the .part is already complete
                tmp.replace(dest)
                return
            resp.raise_for_status()
            if resume_from > 0 and resp.status_code != 206:
                resume_from = 0  # server ignored Range -> restart
            length = int(resp.headers.get("content-length", 0))
            total = resume_from + length if length else 0
            done = resume_from
            with tmp.open("ab" if resume_from > 0 else "wb") as f:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    if cancel is not None and cancel.cancelled:
                        raise DownloadCancelled(dest.name)
                    f.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        tmp.replace(dest)
    finally:
        if owns:
            await client.aclose()
