from pathlib import Path

from colophon.core.identity_scrub import scrub_structural_identity
from colophon.core.models import BookUnit, SeriesRef


def _book():
    return BookUnit.new(source_folder=Path("/lib/x"))


def test_scrub_clears_whitespace_author():
    b = _book()
    b.authors = [" "]
    b.provenance["authors"] = "tag"
    scrub_structural_identity(b)
    assert b.authors == [] and "authors" not in b.provenance


def test_scrub_drops_structural_author_keeps_real():
    b = _book()
    b.authors = ["Real Author", "Chapter"]
    b.provenance["authors"] = "graphing"
    scrub_structural_identity(b)
    assert b.authors == ["Real Author"] and b.provenance["authors"] == "graphing"


def test_scrub_clears_structural_title():
    b = _book()
    b.title = "Chapter"
    b.provenance["title"] = "directory"
    scrub_structural_identity(b)
    assert b.title is None and "title" not in b.provenance


def test_scrub_clears_structural_series():
    b = _book()
    b.series = [SeriesRef(name="Chapter", sequence=1.0)]
    b.provenance["series"] = "filename"
    scrub_structural_identity(b)
    assert b.series == [] and "series" not in b.provenance


def test_scrub_preserves_manual_and_match():
    b = _book()
    b.authors = ["Chapter"]
    b.provenance["authors"] = "manual"
    scrub_structural_identity(b)
    assert b.authors == ["Chapter"]
    b2 = _book()
    b2.title = "Part"
    b2.provenance["title"] = "audnexus"
    scrub_structural_identity(b2)
    assert b2.title == "Part"


def test_scrub_leaves_real_values():
    b = _book()
    b.title = "Elantris"
    b.authors = ["Brandon Sanderson"]
    b.series = [SeriesRef(name="Mistborn", sequence=1.0)]
    for f in ("title", "authors", "series"):
        b.provenance[f] = "tag"
    scrub_structural_identity(b)
    assert b.title == "Elantris"
    assert b.authors == ["Brandon Sanderson"]
    assert b.series[0].name == "Mistborn"
