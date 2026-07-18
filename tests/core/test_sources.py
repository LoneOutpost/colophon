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


def test_isbn_defaults_to_none():
    from colophon.core.sources import SourceQuery, SourceResult
    assert SourceResult(provider="x").isbn is None
    assert SourceQuery().isbn is None


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


def test_audiobook_provider_set_membership():
    from colophon.core.sources import AUDIOBOOK_PROVIDERS
    for p in ("audnexus", "audible", "soundbooththeater", "audioteka", "librofm",
              "graphicaudio", "librivox", "bigfinish", "dreifragezeichen"):
        assert p in AUDIOBOOK_PROVIDERS
    for p in ("hardcover", "goodreads", "openlibrary", "googlebooks", "internetarchive",
              "thalia", "bookbeat", "storytel", "deezer", "ardaudiothek", "unknown"):
        assert p not in AUDIOBOOK_PROVIDERS


def test_edition_fields_unchecked_for_nonaudiobook_source():
    from colophon.core.sources import unchecked_edition_fields
    offered = ["title", "author", "publisher", "isbn", "year"]
    # a print/mixed source: publisher + isbn are offered but should default unchecked
    assert unchecked_edition_fields("hardcover", offered, strict=True) == {"publisher", "isbn"}


def test_edition_fields_all_trusted_for_audiobook_source():
    from colophon.core.sources import unchecked_edition_fields
    offered = ["title", "publisher", "isbn"]
    assert unchecked_edition_fields("soundbooththeater", offered, strict=True) == set()


def test_edition_fields_none_unchecked_when_strict_off():
    from colophon.core.sources import unchecked_edition_fields
    offered = ["publisher", "isbn"]
    assert unchecked_edition_fields("hardcover", offered, strict=False) == set()


def test_edition_fields_only_from_offered():
    from colophon.core.sources import unchecked_edition_fields
    # isbn not offered by this result -> not returned
    assert unchecked_edition_fields("hardcover", ["title", "publisher"], strict=True) == {"publisher"}
