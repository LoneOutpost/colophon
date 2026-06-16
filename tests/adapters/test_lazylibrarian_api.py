import httpx
import pytest

from colophon.adapters.lazylibrarian_api import LazyLibrarianClient, LLError


def _client(handler) -> LazyLibrarianClient:
    transport = httpx.MockTransport(handler)
    return LazyLibrarianClient(
        base_url="http://ll.local",
        api_key="key",
        client=httpx.AsyncClient(transport=transport, base_url="http://ll.local"),
    )


async def test_find_book_passes_cmd_and_apikey():
    def handler(req):
        assert req.url.path == "/api"
        assert req.url.params["cmd"] == "findBook"
        assert req.url.params["apikey"] == "key"
        assert req.url.params["name"] == "Dune"
        return httpx.Response(200, json=[{"bookid": "1", "bookname": "Dune"}])
    results = await _client(handler).find_book("Dune")
    assert results == [{"bookid": "1", "bookname": "Dune"}]


async def test_find_book_non_list_returns_empty():
    # A non-list JSON body (e.g. an error object) yields an empty list.
    src = _client(lambda req: httpx.Response(200, json={"x": 1}))
    assert await src.find_book("x") == []


async def test_ping_true_on_index():
    src = _client(lambda req: httpx.Response(200, json={"data": []}))
    assert await src.ping() is True


async def test_ping_false_when_unreachable(monkeypatch):
    import tenacity

    # neutralize tenacity backoff so the exhausted-retry path is instant
    monkeypatch.setattr(LazyLibrarianClient._cmd.retry, "wait", tenacity.wait_none())

    def handler(req):
        raise httpx.ConnectError("refused")
    assert await _client(handler).ping() is False


async def test_http_error_raises_llerror():
    src = _client(lambda req: httpx.Response(403, text="bad apikey"))
    with pytest.raises(LLError):
        await src.find_book("Dune")
