import httpx

from colophon.adapters.sources.openlibrary import OpenLibrarySource
from colophon.core.sources import SourceQuery

_DOCS = {
    "docs": [
        {
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "first_publish_year": 1965,
            "cover_i": 12345,
            "key": "/works/OL893415W",
        }
    ]
}


def _source(handler) -> OpenLibrarySource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openlibrary.org")
    return OpenLibrarySource(client=client)


async def test_search_normalizes_docs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search.json"
        assert request.url.params["title"] == "Dune"
        return httpx.Response(200, json=_DOCS)

    src = _source(handler)
    results = await src.search(SourceQuery(title="Dune", author="Frank Herbert"))
    assert src.name == "openlibrary"
    assert len(results) == 1
    r = results[0]
    assert r.provider == "openlibrary"
    assert r.title == "Dune"
    assert r.authors == ["Frank Herbert"]
    assert r.publish_year == 1965
    assert r.cover_url == "https://covers.openlibrary.org/b/id/12345-L.jpg"


async def test_search_without_title_returns_empty():
    src = _source(lambda req: httpx.Response(200, json={"docs": []}))
    assert await src.search(SourceQuery(asin="B0")) == []


async def test_http_error_returns_empty_not_raise():
    src = _source(lambda req: httpx.Response(500, text="boom"))
    assert await src.search(SourceQuery(title="Dune")) == []


async def test_retries_on_transport_error_then_gives_up(monkeypatch):
    import tenacity

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    src = _source(handler)
    # neutralize backoff so the test is instant (auto-restored by monkeypatch)
    monkeypatch.setattr(src._get.__func__.retry, "wait", tenacity.wait_none())
    assert await src.search(SourceQuery(title="Dune")) == []
    assert calls["n"] == 3
