import httpx

from colophon.adapters.sources.audnexus import AudnexusSource
from colophon.core.sources import SourceQuery

_BOOK = {  # api.audnex.us /books/{asin} shape
    "asin": "B002V1A0WE",
    "title": "Dune",
    "authors": [{"name": "Frank Herbert"}],
    "narrators": [{"name": "Scott Brick"}],
    "seriesPrimary": {"name": "Dune", "position": "1"},
    "releaseDate": "2007-08-07T00:00:00.000Z",
    "image": "https://m.media-amazon.com/cover.jpg",
    "summary": "A desert planet.",
}


def _source(handler, **kwargs) -> AudnexusSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AudnexusSource(client=client, **kwargs)


async def test_search_by_asin_normalizes_book():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.audnex.us"
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


async def test_title_search_uses_audible_catalog_then_audnex():
    catalog = {"products": [{"asin": "B002V1A0WE"}, {"asin": "B0DELISTED"}]}
    seen = {"keywords": None, "asins": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.audible.com":
            assert request.url.path == "/1.0/catalog/products"
            seen["keywords"] = request.url.params.get("keywords")
            return httpx.Response(200, json=catalog)
        assert request.url.host == "api.audnex.us"
        asin = request.url.path.rsplit("/", 1)[-1]
        seen["asins"].append(asin)
        if asin == "B0DELISTED":
            return httpx.Response(404, json={"error": "delisted"})
        return httpx.Response(200, json=_BOOK)

    src = _source(handler)
    results = await src.search(SourceQuery(title="Dune", author="Frank Herbert"))
    assert "Dune" in (seen["keywords"] or "") and "Frank Herbert" in seen["keywords"]
    assert set(seen["asins"]) == {"B002V1A0WE", "B0DELISTED"}
    assert [r.title for r in results] == ["Dune"]  # the delisted ASIN is skipped


async def test_title_search_empty_catalog_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.audible.com":
            return httpx.Response(200, json={"products": []})
        return httpx.Response(200, json=_BOOK)

    src = _source(handler)
    assert await src.search(SourceQuery(title="Nothing Here")) == []


async def test_catalog_caps_to_max_results():
    products = [{"asin": f"B{i:09d}"} for i in range(10)]
    looked_up = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.audible.com":
            return httpx.Response(200, json={"products": products})
        looked_up.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=_BOOK)

    src = _source(handler, max_results=3)
    await src.search(SourceQuery(title="Many Matches"))
    assert len(looked_up) == 3  # only the top 3 ASINs are resolved


async def test_search_without_asin_or_title_returns_empty():
    src = _source(lambda req: httpx.Response(200, json=_BOOK))
    assert await src.search(SourceQuery(narrators=[])) == []  # no asin, no title


async def test_not_found_asin_returns_empty():
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
    monkeypatch.setattr(src._book_get.__func__.retry, "wait", tenacity.wait_none())
    assert await src.search(SourceQuery(asin="B0")) == []
    assert calls["n"] == 3


async def test_search_parses_genres_and_tags_by_type():
    book = {**_BOOK, "genres": [
        {"asin": "1", "name": "Science Fiction & Fantasy", "type": "genre"},
        {"asin": "2", "name": "Fantasy", "type": "genre"},
        {"asin": "3", "name": "Epic", "type": "tag"},
        {"asin": "4", "type": "genre"},  # no name -> skipped
        {"name": "Mystery"},             # no type -> skipped
    ]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=book)

    src = _source(handler)
    (r,) = await src.search(SourceQuery(asin="B002V1A0WE"))
    assert r.genres == ["Science Fiction & Fantasy", "Fantasy"]
    assert r.tags == ["Epic"]


async def test_search_no_genres_key_yields_empty_lists():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BOOK)

    src = _source(handler)
    (r,) = await src.search(SourceQuery(asin="B002V1A0WE"))
    assert r.genres == []
    assert r.tags == []


_CHAPTERS = {
    "asin": "B002V1A0WE",
    "runtimeLengthMs": 3_600_000,
    "chapters": [
        {"startOffsetMs": 0, "lengthMs": 120_000, "title": "Introduction"},
        {"startOffsetMs": 120_000, "lengthMs": 3_480_000, "title": ""},
    ],
}


async def test_fetch_chapters_maps_offsets_and_titles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/B002V1A0WE/chapters"
        return httpx.Response(200, json=_CHAPTERS)

    src = _source(handler)
    fetch = await src.fetch_chapters("B002V1A0WE")
    assert fetch is not None
    assert fetch.runtime_ms == 3_600_000
    assert [c.title for c in fetch.chapters] == ["Introduction", "Chapter 2"]
    assert (fetch.chapters[0].start_ms, fetch.chapters[0].end_ms) == (0, 120_000)
    assert (fetch.chapters[1].start_ms, fetch.chapters[1].end_ms) == (120_000, 3_600_000)


async def test_fetch_chapters_404_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    src = _source(handler)
    assert await src.fetch_chapters("X") is None


async def test_fetch_chapters_missing_key_yields_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"runtimeLengthMs": 1000})

    src = _source(handler)
    fetch = await src.fetch_chapters("X")
    assert fetch is not None and fetch.chapters == []


def test_to_result_runtime_and_format():
    src = _source(lambda req: httpx.Response(200, json={}))

    r = src._to_result({
        "title": "Dune", "authors": [{"name": "Frank Herbert"}],
        "runtimeLengthMin": 1260, "formatType": "unabridged", "asin": "B000",
    })
    assert r.runtime_ms == 1260 * 60000
    assert r.abridged is False

    r2 = src._to_result({"title": "X", "formatType": "abridged"})
    assert r2.runtime_ms is None
    assert r2.abridged is True

    r3 = src._to_result({"title": "Y"})
    assert r3.runtime_ms is None and r3.abridged is None
