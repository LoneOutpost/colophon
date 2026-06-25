from pathlib import Path

from colophon.core.models import BookState, BookUnit, SourceFile
from colophon.core.stats import library_stats, top_entries


def _book(name, *, state=BookState.DETECTED, authors=None, genres=None, files=()) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest") / name)
    b.title = name
    b.state = state
    if authors:
        b.authors = list(authors)
    if genres:
        b.genres = list(genres)
    b.source_files = [
        SourceFile(path=Path("/ingest") / name / fn, size=size, duration_seconds=secs, ext=".mp3")
        for fn, size, secs in files
    ]
    return b


def test_library_stats_totals_and_state_breakdown():
    books = [
        _book("a", state=BookState.READY, files=[("1.mp3", 1000, 60.0)]),
        _book("b", state=BookState.READY, files=[("1.mp3", 2000, 30.0)]),
        _book("c", state=BookState.NEEDS_REVIEW, files=[("1.mp3", 500, 10.0), ("2.mp3", 500, 5.0)]),
    ]
    s = library_stats(books)
    assert s.total_books == 3
    assert s.total_bytes == 1000 + 2000 + 1000
    assert s.total_duration_ms == (60 + 30 + 15) * 1000
    # enum order, zero-count states omitted; NEEDS_REVIEW precedes READY in the enum
    assert s.by_state == [(BookState.NEEDS_REVIEW, 1), (BookState.READY, 2)]


def test_library_stats_empty():
    s = library_stats([])
    assert s.total_books == 0
    assert s.total_bytes == 0
    assert s.total_duration_ms == 0
    assert s.by_state == []


def test_top_entries_sorted_by_count_then_name():
    books = [
        _book("a", authors=["Sanderson"]),
        _book("b", authors=["Sanderson"]),
        _book("c", authors=["Herbert"]),
        _book("d", authors=["Adams"]),
    ]
    top = top_entries(books, "author", limit=8)
    assert [(e.name, e.count) for e in top] == [
        ("Sanderson", 2), ("Adams", 1), ("Herbert", 1),
    ]


def test_top_entries_respects_limit():
    books = [_book(f"b{i}", genres=[f"G{i}"]) for i in range(10)]
    assert len(top_entries(books, "genre", limit=3)) == 3
