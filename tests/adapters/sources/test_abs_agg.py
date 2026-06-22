import httpx

from colophon.adapters.sources.abs_agg import AbsAggSource, discover_providers
from colophon.core.sources import SourceQuery


def _source(handler, provider="hardcover", label="Hardcover") -> AbsAggSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://abs-agg")
    return AbsAggSource(provider=provider, label=label, client=client)


def test_name_is_provider_id():
    assert _source(lambda r: httpx.Response(200, json={})).name == "hardcover"


async def test_search_maps_match_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/hardcover/search"
        assert request.url.params["title"] == "Dune"
        assert request.url.params["author"] == "Herbert"
        return httpx.Response(200, json={"matches": [
            {
                "title": "Dune", "subtitle": "Book One", "author": "Frank Herbert",
                "narrator": "Scott Brick", "publisher": "Ace", "publishedYear": "1965",
                "description": "Epic.", "cover": "http://c/x.jpg",
                "isbn": "9780441013593", "asin": "B002V0K6D8",
                "genres": ["Science Fiction"], "tags": ["Classic"],
                "series": [{"series": "Dune", "sequence": "1"},
                           {"series": "Dune Universe", "sequence": "14"}],
                "language": "English", "duration": 75600,
            }
        ]})

    results = await _source(handler).search(SourceQuery(title="Dune", author="Herbert"))
    assert len(results) == 1
    r = results[0]
    assert r.provider == "hardcover"
    assert r.title == "Dune" and r.subtitle == "Book One"
    assert r.authors == ["Frank Herbert"]
    assert r.narrators == ["Scott Brick"]
    assert r.publisher == "Ace" and r.language == "English"
    assert r.publish_year == 1965
    assert r.isbn == "9780441013593" and r.asin == "B002V0K6D8"
    assert r.genres == ["Science Fiction"] and r.tags == ["Classic"]
    assert r.series_name == "Dune" and r.series_sequence == 1.0
    assert r.cover_url == "http://c/x.jpg"
    assert r.runtime_ms == 75600 * 1000


async def test_search_omits_author_when_absent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"matches": []})

    await _source(handler).search(SourceQuery(title="Dune"))
    assert captured["params"] == {"title": "Dune"}


async def test_search_without_title_returns_empty():
    src = _source(lambda r: httpx.Response(200, json={"matches": []}))
    assert await src.search(SourceQuery(author="x")) == []


async def test_search_non_200_returns_empty():
    src = _source(lambda r: httpx.Response(500, text="boom"))
    assert await src.search(SourceQuery(title="anything")) == []


async def test_search_http_error_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await _source(handler).search(SourceQuery(title="x")) == []


async def test_partial_match_missing_optional_fields():
    body = {"matches": [{"title": "Sherlock Holmes", "author": "Doyle"}]}
    r = (await _source(handler=lambda req: httpx.Response(200, json=body)).search(
        SourceQuery(title="sherlock")))[0]
    assert r.title == "Sherlock Holmes"
    assert r.authors == ["Doyle"]
    assert r.narrators == [] and r.isbn is None and r.series_name is None
    assert r.publish_year is None and r.runtime_ms is None


def test_discover_providers_registers_available(monkeypatch):
    import colophon.adapters.sources.abs_agg as mod

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/providers"
        return httpx.Response(200, json=[
            {"id": "hardcover", "name": "Hardcover", "available": True},
            {"id": "goodreads", "name": "Goodreads", "available": True},
            {"id": "broken", "name": "Broken", "available": False},
        ])

    real_client = httpx.Client
    monkeypatch.setattr(
        mod.httpx, "Client",
        lambda _c=real_client, **kw: _c(transport=httpx.MockTransport(handler), base_url="http://abs-agg"),
    )
    sources = discover_providers("http://abs-agg")
    assert [s.name for s in sources] == ["hardcover", "goodreads"]
    assert sources[0].label == "Hardcover"


def test_discover_providers_unreachable_returns_empty(monkeypatch):
    import colophon.adapters.sources.abs_agg as mod

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    real_client = httpx.Client
    monkeypatch.setattr(
        mod.httpx, "Client",
        lambda _c=real_client, **kw: _c(transport=httpx.MockTransport(handler), base_url="http://abs-agg"),
    )
    assert discover_providers("http://abs-agg") == []


def test_discover_providers_blank_url_returns_empty():
    assert discover_providers("") == []
    assert discover_providers(None) == []
