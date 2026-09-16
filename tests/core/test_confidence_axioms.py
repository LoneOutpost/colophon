from colophon.core.confidence_axioms import (
    W_FOLDER,
    W_MANUAL,
    W_MATCH,
    source_weight,
    tag_completeness,
)
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


def test_source_weight_ranks_visible_sources_above_tags():
    rich = EmbeddedTags(title="t", album="a", artist="x", narrator="n",
                        series="s", year=1, genre="g", description="d", asin="B")
    assert source_weight("manual", rich, 0.0) == W_MANUAL
    assert source_weight("audnexus", rich, 0.0) == W_MATCH
    assert source_weight("directory", rich, 0.0) == W_FOLDER
    # a folder outranks even a perfectly complete tag
    assert source_weight("directory", rich, 0.0) >= source_weight("tag", rich, 0.0)


def test_source_weight_scales_a_tag_by_its_completeness():
    bare = EmbeddedTags(title="t")
    rich = EmbeddedTags(title="t", album="a", artist="x", narrator="n",
                        series="s", year=1, genre="g", description="d", asin="B")
    assert source_weight("tag", bare, 0.0) < source_weight("tag", rich, 0.0)
    assert source_weight("tag", rich, 0.0) == W_FOLDER          # 0.30 + 0.45 == 0.75


def test_source_weight_treats_every_real_provider_as_a_match():
    # Regression: _STRONG_ID_PROV used the string "match", which Provenance never emits, so a matched
    # field was scored as a folder guess.
    rich = EmbeddedTags()
    for provider in ("audnexus", "audible", "hardcover", "openlibrary", "googlebooks"):
        assert source_weight(provider, rich, 0.0) == W_MATCH, provider


def test_source_weight_falls_back_to_the_graph_node():
    assert source_weight("graphing", EmbeddedTags(), 0.5) == 0.4      # 0.5 * 0.80
    assert source_weight(None, EmbeddedTags(), 0.5) == 0.4
