from pathlib import Path

from colophon.core.models import BookUnit, SeriesRef
from colophon.core.navigator import build_library_tree


def _book(name, *, author=None, series=None, seq=None, confidence=0.0) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest") / name)
    b.title = name
    b.confidence = confidence
    if author:
        b.authors = [author]
    if series:
        b.series = [SeriesRef(name=series, sequence=seq)]
    return b


def test_build_library_tree_groups_and_sorts():
    a1 = _book("Way of Kings", author="Sanderson", series="Stormlight", seq=1.0)
    a2 = _book("Words of Radiance", author="Sanderson", series="Stormlight", seq=2.0)
    standalone = _book("Warbreaker", author="Sanderson")
    other = _book("Dune", author="Herbert")

    tree = build_library_tree([a2, standalone, other, a1])

    assert [a.name for a in tree.authors] == ["Herbert", "Sanderson"]
    sanderson = tree.authors[1]
    assert [s.name for s in sanderson.series] == ["Stormlight"]
    # Series books sorted by sequence regardless of input order.
    assert [b.title for b in sanderson.series[0].books] == ["Way of Kings", "Words of Radiance"]
    assert [b.title for b in sanderson.standalone] == ["Warbreaker"]


def test_build_library_tree_needs_id_sorted_by_confidence():
    hi = _book("blob-hi", confidence=40.0)
    lo = _book("blob-lo", confidence=10.0)
    tree = build_library_tree([hi, lo])
    assert tree.authors == []
    assert [b.confidence for b in tree.needs_id] == [10.0, 40.0]


def test_build_library_tree_falls_back_to_series_name_without_author():
    b = _book("orphan", series="Lonely Series", seq=1.0)
    tree = build_library_tree([b])
    assert [a.name for a in tree.authors] == ["Lonely Series"]
