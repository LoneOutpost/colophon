from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookState, BookUnit, SeriesRef
from colophon.core.sources import SourceQuery, SourceResult
from colophon.services.identify import identify


class _StubSource:
    def __init__(self, name: str, results: list[SourceResult]):
        self.name = name
        self._results = results
        self.seen: SourceQuery | None = None

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        self.seen = query
        return self._results


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _book() -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest/Dune"))
    b.title, b.authors, b.asin = "Dune", ["Frank Herbert"], "B002V1A0WE"
    return b


async def test_high_confidence_marks_ready_and_persists(tmp_path):
    repo = _repo(tmp_path)
    book = _book()
    repo.upsert(book)
    src = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE")])

    out = await identify(repo, book, [src], threshold=75.0)

    assert out.state == BookState.READY
    assert out.confidence >= 90
    assert out.confidence_signals  # signals recorded
    assert repo.get(book.id).state == BookState.READY
    # the query was built from the candidate
    assert src.seen.asin == "B002V1A0WE"


async def test_low_confidence_marks_needs_review(tmp_path):
    repo = _repo(tmp_path)
    book = BookUnit.new(source_folder=Path("/ingest/mystery"))
    book.title = "Unknown"
    repo.upsert(book)
    src = _StubSource("openlibrary", [])

    out = await identify(repo, book, [src], threshold=75.0)
    assert out.state == BookState.NEEDS_REVIEW
    assert out.confidence == 0.0


async def test_failing_source_does_not_abort(tmp_path):
    repo = _repo(tmp_path)
    book = _book()
    repo.upsert(book)

    class _Boom:
        name = "boom"
        async def search(self, query): raise RuntimeError("down")

    good = _StubSource("audnexus", [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE")])
    out = await identify(repo, book, [_Boom(), good], threshold=75.0)
    assert out.state == BookState.READY  # one source failing didn't abort


async def test_high_confidence_without_identity_stays_needs_review(tmp_path):
    repo = _repo(tmp_path)
    book = BookUnit.new(source_folder=Path("/ingest/X"))
    book.title = "X"  # no authors, no series -> has_identity is False
    repo.upsert(book)
    src = _StubSource("p", [SourceResult(provider="p", title="X")])

    out = await identify(repo, book, [src], threshold=0.0)
    assert out.state == BookState.NEEDS_REVIEW


async def test_series_only_identity_can_be_ready(tmp_path):
    repo = _repo(tmp_path)
    book = BookUnit.new(source_folder=Path("/ingest/wor"))
    book.title = "Words of Radiance"
    book.series = [SeriesRef(name="Stormlight", sequence=1.0)]  # identity via series, no authors
    repo.upsert(book)
    src = _StubSource("p", [SourceResult(provider="p", title="Words of Radiance")])

    out = await identify(repo, book, [src], threshold=0.0)
    assert out.state == BookState.READY


def test_query_for_includes_series_name():
    from pathlib import Path

    from colophon.core.models import BookUnit, SeriesRef
    from colophon.services.identify import _query_for

    book = BookUnit.new(source_folder=Path("/x"))
    book.title = "The Final Empire"
    book.authors = ["Brandon Sanderson"]
    book.series = [SeriesRef(name="Mistborn", sequence=1.0)]
    q = _query_for(book)
    assert q.series == "Mistborn" and q.title == "The Final Empire" and q.author == "Brandon Sanderson"
