import asyncio

from colophon.core.models import BookUnit, SeriesRef
from colophon.core.sources import SourceQuery, SourceResult
from colophon.services.matching import gather_matches, query_for_book


class _OkSource:
    def __init__(self, name, results):
        self.name = name
        self._results = results

    async def search(self, query):
        return self._results


class _BoomSource:
    name = "boom"

    async def search(self, query):
        raise RuntimeError("source down")


def test_query_for_book_uses_first_author_and_series(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "Dune"
    b.authors = ["Frank Herbert", "Someone Else"]
    b.series = [SeriesRef(name="Dune")]
    b.asin = "B002V1A0WE"
    q = query_for_book(b)
    assert q.title == "Dune"
    assert q.author == "Frank Herbert"
    assert q.series == "Dune"
    assert q.asin == "B002V1A0WE"


def test_query_for_book_none_when_fields_absent(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "x")
    q = query_for_book(b)
    assert q.title is None
    assert q.author is None
    assert q.series is None


def test_gather_matches_skips_failing_source():
    ok = _OkSource("ok", [SourceResult(provider="ok", title="Dune")])
    results = asyncio.run(gather_matches([_BoomSource(), ok], SourceQuery(title="Dune")))
    assert [r.provider for r in results] == ["ok"]


def test_gather_matches_flattens_all_sources():
    a = _OkSource("a", [SourceResult(provider="a", title="A")])
    b = _OkSource("b", [SourceResult(provider="b", title="B1"), SourceResult(provider="b", title="B2")])
    results = asyncio.run(gather_matches([a, b], SourceQuery(title="x")))
    assert {r.provider for r in results} == {"a", "b"}
    assert len(results) == 3


def test_gather_matches_empty_when_no_sources():
    results = asyncio.run(gather_matches([], SourceQuery(title="x")))
    assert results == []
