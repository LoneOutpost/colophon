from colophon.core.models import EmbeddedTags
from colophon.core.tag_validation import validate_tags


def test_clean_tags_produce_no_warnings():
    tags = EmbeddedTags(title="Mistborn", artist="Brandon Sanderson", year=2006, sequence=1.0)
    assert validate_tags(tags) == []


def test_missing_title_warns():
    assert any("title" in w.lower() for w in validate_tags(EmbeddedTags(artist="x")))


def test_implausible_year_warns():
    assert any("year" in w.lower() for w in validate_tags(EmbeddedTags(title="x", year=99)))


def test_negative_sequence_warns():
    assert any("sequence" in w.lower() for w in validate_tags(EmbeddedTags(title="x", sequence=-1.0)))
