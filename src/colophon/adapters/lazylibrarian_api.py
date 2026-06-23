"""Async, read-only LazyLibrarian API client (status/lookup only — no write-back)."""

from __future__ import annotations

import httpx

from colophon.adapters.http import HTTP_RETRY


class LLError(RuntimeError):
    """A LazyLibrarian request failed (4xx or exhausted retries)."""




class LazyLibrarianClient:
    def __init__(self, *, base_url: str, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0)

    @HTTP_RETRY
    async def _cmd(self, command: str, **params: str) -> httpx.Response:
        query = {"cmd": command, "apikey": self._api_key, **params}
        return await self._client.get("/api", params=query)

    async def ping(self) -> bool:
        try:
            resp = await self._cmd("getIndex")
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    async def find_book(self, term: str) -> list[dict]:
        """Read-only lookup by name. Returns raw LL book dicts, or [] if none."""
        try:
            resp = await self._cmd("findBook", name=term)
        except httpx.HTTPError as e:
            raise LLError(f"findBook failed: {e}") from e
        if resp.status_code >= 400:
            raise LLError(f"findBook returned {resp.status_code}")
        body = resp.json() if resp.text else []
        return body if isinstance(body, list) else []
