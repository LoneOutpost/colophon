"""Shared HTTP concerns for the adapter layer.

`HTTP_RETRY` is the single retry policy every outbound HTTP adapter uses, so
attempt count, backoff, and the retryable-exception set are tuned in one place
rather than drifting across providers.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Retry transient transport errors (DNS, connect, read timeouts) up to 3 attempts
# with exponential backoff; reraise the last error rather than tenacity's wrapper.
HTTP_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


@HTTP_RETRY
async def _retried_get(
    client: httpx.AsyncClient, path: str, params: dict[str, object]
) -> httpx.Response:
    return await client.get(path, params=params)


async def get_json_list(
    client: httpx.AsyncClient, path: str, *, params: dict[str, object], key: str
) -> list[Any]:
    """GET `path` (with `HTTP_RETRY`) and return `body[key]` as a list.

    Encapsulates the search shape shared by the flat-list source adapters: a
    transport error (`httpx.HTTPError`) or a `>=400` response yields `[]` (a failed
    lookup is "no results"), and the list is unwrapped as
    `(body or {}).get(key) or []` so a missing or empty `key` also yields `[]`."""
    try:
        resp = await _retried_get(client, path, params)
    except httpx.HTTPError:
        return []
    if resp.status_code >= 400:
        return []
    return (resp.json() or {}).get(key) or []
