import httpx

from colophon.adapters.sources.audnexus import AudnexusSource
from colophon.core.sources import SourceQuery

_BOOK = {
    "asin": "B002V1A0WE",
    "title": "Dune",
    "authors": [{"name": "Frank Herbert"}],
    "narrators": [{"name": "Scott Brick"}],
    "seriesPrimary": {"name": "Dune", "position": "1"},
    "releaseDate": "2007-08-07T00:00:00.000Z",
    "image": "https://m.media-amazon.com/cover.jpg",
    "summary": "A desert planet.",
}


def _source(handler) -> AudnexusSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.audnex.us")
    return AudnexusSource(client=client)


async def test_search_by_asin_normalizes_book():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/B002V1A0WE"
        return httpx.Response(200, json=_BOOK)

    src = _source(handler)
    results = await src.search(SourceQuery(asin="B002V1A0WE"))
    assert src.name == "audnexus"
    assert len(results) == 1
    r = results[0]
    assert r.title == "Dune"
    assert r.authors == ["Frank Herbert"]
    assert r.narrators == ["Scott Brick"]
    assert r.series_name == "Dune"
    assert r.series_sequence == 1.0
    assert r.publish_year == 2007
    assert r.asin == "B002V1A0WE"


async def test_search_without_asin_returns_empty():
    src = _source(lambda req: httpx.Response(200, json=_BOOK))
    assert await src.search(SourceQuery(title="Dune")) == []


async def test_not_found_returns_empty():
    src = _source(lambda req: httpx.Response(404, text="not found"))
    assert await src.search(SourceQuery(asin="BOGUS")) == []


async def test_retries_on_transport_error_then_gives_up(monkeypatch):
    import tenacity

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    src = _source(handler)
    # neutralize backoff so the test is instant (auto-restored by monkeypatch)
    monkeypatch.setattr(src._get.__func__.retry, "wait", tenacity.wait_none())
    assert await src.search(SourceQuery(asin="B0")) == []
    assert calls["n"] == 3
