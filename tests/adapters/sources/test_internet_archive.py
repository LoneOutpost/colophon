import httpx

from colophon.adapters.sources.internet_archive import (
    InternetArchiveSource,
    _parse_narrators,
    _parse_runtime,
)
from colophon.core.sources import SourceQuery


def _source(handler) -> InternetArchiveSource:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://archive.org"
    )
    return InternetArchiveSource(client=client)


def test_parse_runtime():
    assert _parse_runtime("7:34:27") == 27267000
    assert _parse_runtime("58:03") == 3483000
    assert _parse_runtime("90") == 90000
    assert _parse_runtime("abc") is None
    assert _parse_runtime(None) is None


def test_parse_narrators():
    assert _parse_narrators("A classic. Read by Jane Doe.") == ["Jane Doe"]
    assert _parse_narrators("Narrated by Alice and Bob") == ["Alice", "Bob"]
    assert _parse_narrators("Reader: Kara Shallenberg") == ["Kara Shallenberg"]
    assert _parse_narrators("No cue here at all") == []
    assert _parse_narrators(None) == []


def test_to_result_maps_fields():
    src = _source(lambda r: httpx.Response(200, json={}))
    doc = {"identifier": "dune_lv", "title": "Dune", "creator": ["Frank Herbert"], "year": "2009",
           "subject": ["Science fiction"]}
    meta = {"description": "An epic. Read by Scott Brick.", "runtime": "21:02:00"}
    r = src._to_result(doc, meta)
    assert r.title == "Dune"
    assert r.authors == ["Frank Herbert"]
    assert r.narrators == ["Scott Brick"]
    assert r.publish_year == 2009
    assert r.runtime_ms == 21 * 3600000 + 2 * 60000
    assert r.cover_url == "https://archive.org/services/img/dune_lv"
    assert r.genres == ["Science fiction"]


def test_to_result_creator_as_string():
    src = _source(lambda r: httpx.Response(200, json={}))
    r = src._to_result({"identifier": "x", "title": "T", "creator": "Solo Author"}, {})
    assert r.authors == ["Solo Author"]


async def test_search_returns_candidates_with_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/advancedsearch.php":
            assert "librivoxaudio" in request.url.params["q"]
            return httpx.Response(200, json={"response": {"docs": [
                {"identifier": "a1", "title": "Aesop", "creator": "Aesop", "year": "2008"},
                {"identifier": "b2", "title": "Beowulf", "creator": "Unknown"},
            ]}})
        if request.url.path == "/metadata/a1":
            return httpx.Response(200, json={"metadata": {"description": "Read by Kara.", "runtime": "1:00:00"}})
        if request.url.path == "/metadata/b2":
            return httpx.Response(200, json={"metadata": {"runtime": "2:30:00"}})
        return httpx.Response(404)

    src = _source(handler)
    results = await src.search(SourceQuery(title="aesop", author="aesop"))
    assert [r.title for r in results] == ["Aesop", "Beowulf"]
    assert results[0].narrators == ["Kara"]
    assert results[0].runtime_ms == 3600000
    assert results[1].runtime_ms == 9000000


async def test_search_empty_title_returns_empty():
    src = _source(lambda r: httpx.Response(200, json={}))
    assert await src.search(SourceQuery(title=None)) == []


async def test_search_http_error_returns_empty():
    src = _source(lambda r: httpx.Response(500))
    assert await src.search(SourceQuery(title="anything")) == []


async def test_metadata_failure_still_yields_candidate():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/advancedsearch.php":
            return httpx.Response(200, json={"response": {"docs": [
                {"identifier": "c3", "title": "Candide", "creator": "Voltaire"},
            ]}})
        return httpx.Response(500)  # metadata fails

    src = _source(handler)
    results = await src.search(SourceQuery(title="candide"))
    assert len(results) == 1
    assert results[0].title == "Candide"
    assert results[0].runtime_ms is None
    assert results[0].narrators == []
