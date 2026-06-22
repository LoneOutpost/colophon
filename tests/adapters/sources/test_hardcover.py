import httpx

from colophon.adapters.sources.hardcover import HardcoverSource
from colophon.core.sources import SourceQuery

_BODY = {
    "data": {
        "books": [
            {
                "title": "The Long Dark Tea-Time of the Soul",
                "release_year": 1988,
                "description": "Dirk Gently returns.",
                "contributions": [{"author": {"name": "Douglas Adams"}}],
                "image": {"url": "http://hc/cover.jpg"},
            }
        ]
    }
}


def _source(handler) -> HardcoverSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.hardcover.app")
    return HardcoverSource(token="hc-token", client=client)


async def test_search_posts_graphql_with_bearer():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/graphql"
        assert request.headers["Authorization"] == "Bearer hc-token"
        body = request.read().decode()
        assert "books" in body and "Tea-Time" in body
        return httpx.Response(200, json=_BODY)

    src = _source(handler)
    results = await src.search(SourceQuery(title="The Long Dark Tea-Time of the Soul", author="Douglas Adams"))
    assert src.name == "hardcover"
    assert len(results) == 1
    r = results[0]
    assert r.provider == "hardcover"
    assert r.title == "The Long Dark Tea-Time of the Soul"
    assert r.authors == ["Douglas Adams"]
    assert r.publish_year == 1988
    assert r.cover_url == "http://hc/cover.jpg"
    assert r.description == "Dirk Gently returns."


async def test_title_search_has_no_isbn_when_editions_absent():
    src = _source(lambda req: httpx.Response(200, json=_BODY))
    results = await src.search(SourceQuery(title="The Long Dark Tea-Time of the Soul"))
    assert results[0].isbn is None


async def test_title_search_parses_isbn_from_default_edition():
    body = {
        "data": {
            "books": [
                {
                    "title": "Dune",
                    "release_year": 1965,
                    "contributions": [{"author": {"name": "Frank Herbert"}}],
                    "default_physical_edition": {"isbn_13": "9780441172719", "isbn_10": None},
                    "default_ebook_edition": {"isbn_13": None, "isbn_10": None},
                }
            ]
        }
    }
    src = _source(lambda req: httpx.Response(200, json=body))
    results = await src.search(SourceQuery(title="Dune"))
    assert results[0].isbn == "9780441172719"


async def test_search_by_isbn_queries_editions_and_maps_book():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        captured["body"] = body
        return httpx.Response(200, json={
            "data": {
                "editions": [
                    {
                        "isbn_13": "9780306406157",
                        "isbn_10": "0306406152",
                        "book": {
                            "title": "Compilers",
                            "release_year": 1986,
                            "contributions": [{"author": {"name": "Alfred Aho"}}],
                        },
                    }
                ]
            }
        })

    src = _source(handler)
    results = await src.search(SourceQuery(isbn="0306406152"))
    assert "editions" in captured["body"]
    assert "0306406152" in captured["body"]
    assert len(results) == 1
    r = results[0]
    assert r.title == "Compilers"
    assert r.authors == ["Alfred Aho"]
    assert r.isbn == "9780306406157"  # ISBN-13 preferred from the matched edition


async def test_search_without_title_returns_empty():
    src = _source(lambda req: httpx.Response(200, json={"data": {"books": []}}))
    assert await src.search(SourceQuery(asin="B0")) == []


async def test_graphql_errors_body_returns_empty():
    src = _source(lambda req: httpx.Response(200, json={"errors": [{"message": "bad query"}]}))
    assert await src.search(SourceQuery(title="X")) == []


async def test_http_error_returns_empty():
    src = _source(lambda req: httpx.Response(401, text="unauthorized"))
    assert await src.search(SourceQuery(title="Dune")) == []
