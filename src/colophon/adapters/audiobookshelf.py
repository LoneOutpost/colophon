"""Async AudiobookShelf client — only the endpoints Colophon uses."""

from __future__ import annotations

import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class AbsError(RuntimeError):
    """An AudiobookShelf request failed (auth, 4xx, or exhausted retries)."""


class AbsLibrary(BaseModel):
    id: str
    name: str


_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


class AbsClient:
    def __init__(self, *, base_url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    @_RETRY
    async def _get(self, path: str) -> httpx.Response:
        return await self._client.get(path)

    @_RETRY
    async def _post(self, path: str) -> httpx.Response:
        return await self._client.post(path)

    async def ping(self) -> bool:
        try:
            resp = await self._get("/ping")
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    async def list_libraries(self) -> list[AbsLibrary]:
        try:
            resp = await self._get("/api/libraries")
        except httpx.HTTPError as e:
            raise AbsError(f"list_libraries failed: {e}") from e
        if resp.status_code >= 400:
            raise AbsError(f"list_libraries returned {resp.status_code}")
        libs = (resp.json() or {}).get("libraries") or []
        return [AbsLibrary(id=str(lib["id"]), name=str(lib.get("name", ""))) for lib in libs]

    async def scan_library(self, library_id: str) -> str:
        try:
            resp = await self._post(f"/api/libraries/{library_id}/scan")
        except httpx.HTTPError as e:
            raise AbsError(f"scan_library failed: {e}") from e
        if resp.status_code >= 400:
            raise AbsError(f"scan_library returned {resp.status_code}: {resp.text[:200]}")
        return resp.text.strip()
