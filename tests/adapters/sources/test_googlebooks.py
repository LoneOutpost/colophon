import httpx

from colophon.adapters.sources.googlebooks import GoogleBooksSource
from colophon.core.sources import SourceQuery

_BODY = {
    "items": [
        {
            "volumeInfo": {
                "title": "The Hitchhiker's Guide to the Galaxy",
                "authors": ["Douglas Adams"],
                "publishedDate": "1979-10-12",
                "description": "Don't panic.",
                "imageLinks": {"thumbnail": "http://books/cover.jpg"},
            }
        }
    ]
}


def _source(handler) -> GoogleBooksSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://www.googleapis.com")
    return GoogleBooksSource(client=client)


async def test_search_builds_q_and_normalizes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/v1/volumes"
        q = request.url.params["q"]
        assert "intitle:The Hitchhiker's Guide to the Galaxy" in q
        assert "inauthor:Douglas Adams" in q
        return httpx.Response(200, json=_BODY)

    src = _source(handler)
    results = await src.search(SourceQuery(title="The Hitchhiker's Guide to the Galaxy", author="Douglas Adams"))
    assert src.name == "googlebooks"
    assert len(results) == 1
    r = results[0]
    assert r.provider == "googlebooks"
    assert r.title == "The Hitchhiker's Guide to the Galaxy"
    assert r.authors == ["Douglas Adams"]
    assert r.publish_year == 1979
    assert r.cover_url == "http://books/cover.jpg"
    assert r.description == "Don't panic."


async def test_search_without_title_returns_empty():
    src = _source(lambda req: httpx.Response(200, json={"items": []}))
    assert await src.search(SourceQuery(asin="B0")) == []


async def test_no_items_returns_empty():
    src = _source(lambda req: httpx.Response(200, json={}))
    assert await src.search(SourceQuery(title="Nothing")) == []


async def test_http_error_returns_empty_not_raise():
    src = _source(lambda req: httpx.Response(503, text="rate limited"))
    assert await src.search(SourceQuery(title="Dune")) == []
