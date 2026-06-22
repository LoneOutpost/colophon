from colophon.core.sources import SourceQuery, SourceResult


def test_source_query_holds_optional_fields():
    q = SourceQuery(title="Dune", author="Frank Herbert", asin="B002V1A0WE")
    assert q.title == "Dune"
    assert q.asin == "B002V1A0WE"


def test_source_query_allows_all_none():
    q = SourceQuery()
    assert q.title is None and q.author is None and q.asin is None


def test_source_result_runtime_and_abridged_default_none():
    from colophon.core.sources import SourceResult
    r = SourceResult(provider="x", title="t")
    assert r.runtime_ms is None
    assert r.abridged is None


def test_source_result_round_trips():
    r = SourceResult(
        provider="audnexus",
        title="Dune",
        authors=["Frank Herbert"],
        narrators=["Scott Brick"],
        series_name="Dune",
        series_sequence=1.0,
        publish_year=2007,
        asin="B002V1A0WE",
        cover_url="http://x/cover.jpg",
        description="desc",
        raw={"k": "v"},
    )
    restored = SourceResult.model_validate_json(r.model_dump_json())
    assert restored == r
    assert restored.authors == ["Frank Herbert"]
