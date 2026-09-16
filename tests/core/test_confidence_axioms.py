from colophon.core.confidence_axioms import tag_completeness
from colophon.core.models import EmbeddedTags


def test_tag_completeness_is_zero_for_empty_tags():
    assert tag_completeness(EmbeddedTags()) == 0.0
    assert tag_completeness(None) == 0.0


def test_tag_completeness_rises_with_populated_fields():
    bare = EmbeddedTags(title="Dune")
    rich = EmbeddedTags(title="Dune", album="Dune", artist="Frank Herbert",
                        narrator="Scott Brick", series="Dune", year=1965,
                        genre="SF", description="A long description", asin="B000")
    assert 0.0 < tag_completeness(bare) < 0.2
    assert tag_completeness(rich) == 1.0
    assert tag_completeness(bare) < tag_completeness(rich)


def test_tag_completeness_treats_asin_and_isbn_as_one_field():
    both = EmbeddedTags(asin="B000", isbn="978")
    one = EmbeddedTags(asin="B000")
    assert tag_completeness(both) == tag_completeness(one)
