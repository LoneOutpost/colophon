import httpx

from colophon.adapters.http import get_json_list


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")


async def test_returns_the_keyed_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "dune"
        return httpx.Response(200, json={"docs": [{"a": 1}, {"a": 2}]})

    out = await get_json_list(_client(handler), "/search", params={"q": "dune"}, key="docs")
    assert out == [{"a": 1}, {"a": 2}]


async def test_missing_key_yields_empty_list():
    out = await get_json_list(
        _client(lambda r: httpx.Response(200, json={"other": [1]})),
        "/s",
        params={},
        key="docs",
    )
    assert out == []


async def test_status_4xx_yields_empty_list():
    out = await get_json_list(
        _client(lambda r: httpx.Response(404, json={"docs": [1, 2]})),
        "/s",
        params={},
        key="docs",
    )
    assert out == []


async def test_transport_error_yields_empty_list(monkeypatch):
    import tenacity

    from colophon.adapters import http

    # neutralize backoff so the exhausted-retry path is instant
    monkeypatch.setattr(http._retried_get.retry, "wait", tenacity.wait_none())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    out = await get_json_list(_client(handler), "/s", params={}, key="docs")
    assert out == []
