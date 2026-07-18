import httpx
import pytest

from colophon.adapters.realdebrid import RealDebridClient, RealDebridError


def _client(handler) -> RealDebridClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.real-debrid.com/rest/1.0")
    return RealDebridClient("tok", client=http)


async def test_unrestrict_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json={"filename": "a.mp3", "filesize": 5, "download": "http://d/a"})

    client = _client(handler)
    unr = await client.unrestrict_link("L1")
    assert unr.filename == "a.mp3"
    assert calls["n"] == 2  # retried once


async def test_unrestrict_does_not_retry_on_404():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "unknown link"})

    client = _client(handler)
    with pytest.raises(RealDebridError) as ei:
        await client.unrestrict_link("L1")
    assert ei.value.status_code == 404
    assert calls["n"] == 1  # not retried
