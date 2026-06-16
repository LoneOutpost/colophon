from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.core.models import BookUnit, SeriesRef
from colophon.core.pathscheme import build_target_path, expand_pattern, sanitize_segment


def _book() -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "The Way of Kings"
    b.authors = ["Brandon Sanderson"]
    b.series = [SeriesRef(name="Stormlight Archive", sequence=1.0)]
    b.publish_year = 2010
    return b


def test_expand_common_tokens():
    b = _book()
    assert expand_pattern("$Author/$Series/$Title", b) == "Brandon Sanderson/Stormlight Archive/The Way of Kings"
    assert expand_pattern("$PubYear - $Title", b) == "2010 - The Way of Kings"


def test_padnum_zero_pads_sernum():
    b = _book()
    assert expand_pattern("$Series $PadNum", b) == "Stormlight Archive 01"
    assert expand_pattern("$Series $SerNum", b) == "Stormlight Archive 1"


def test_unknown_token_expands_empty():
    assert expand_pattern("$Bogus$Title", _book()) == "The Way of Kings"


def test_missing_field_expands_empty():
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"
    assert expand_pattern("$Author/$Title", b) == "/Solo"  # empty author segment


def test_sanitize_segment_strips_illegal_chars():
    assert sanitize_segment('a/b:c"d?e') == "abcde"
    assert sanitize_segment("  spaced  ") == "spaced"
    assert sanitize_segment("trailing.") == "trailing"


def test_sanitize_segment_neutralizes_traversal():
    assert sanitize_segment("..") == ""
    assert sanitize_segment(".") == ""
    assert sanitize_segment("???") == ""
    assert sanitize_segment("   ") == ""


def test_build_target_path_uses_single_file_name(tmp_path):
    b = _book()
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Brandon Sanderson" / "The Way of Kings" / "The Way of Kings.m4b"


def test_build_target_path_falls_back_to_title_when_single_file_empty(tmp_path):
    b = _book()
    pats = AudiobookPatterns(folder="$Author", single_file="")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Brandon Sanderson" / "The Way of Kings.m4b"


def test_build_target_path_sanitizes_each_segment(tmp_path):
    b = _book()
    b.authors = ["AC/DC Author"]
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    # the '/' in the author value must NOT create an extra directory level
    assert target.relative_to(tmp_path).parts[0] == "ACDC Author"


def test_build_target_path_authorless_collapses_segment(tmp_path):
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"
    pats = AudiobookPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    # the empty author segment collapses — Path swallows ""
    assert target == tmp_path / "Solo" / "Solo.m4b"
