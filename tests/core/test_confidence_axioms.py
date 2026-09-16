from pathlib import Path

from colophon.core.confidence_axioms import (
    FULL_SUPPORT,
    W_FOLDER,
    W_MANUAL,
    W_MATCH,
    axis_candidates,
    axis_support,
    source_weight,
    tag_completeness,
)
from colophon.core.models import BookUnit, EmbeddedTags, SourceFile


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


def _book(folder="/audio/Frank Herbert/Dune", stem="Dune", **kw):
    b = BookUnit.new(source_folder=Path(folder))
    b.source_files = [SourceFile(path=Path(folder) / f"{stem}.mp3", size=1, duration_seconds=1.0,
                                 ext="mp3", tags=kw.pop("tags", EmbeddedTags()))]
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_axis_candidates_collects_one_vote_per_source():
    b = _book(tags=EmbeddedTags(artist="Frank Herbert"), authors=["Frank Herbert"])
    got = {name: value for value, _weight, name in axis_candidates(b, "author", 0.0)}
    assert got["tag"] == "Frank Herbert"
    assert got["folder"] == "Frank Herbert"      # the parent directory of the book folder


def test_axis_candidates_drops_a_junk_author_so_it_cannot_vote():
    b = _book(tags=EmbeddedTags(artist="Narrated by William Gaminara"),
              authors=["Narrated by William Gaminara"])
    names = {name for _v, _w, name in axis_candidates(b, "author", 0.0)}
    assert "tag" not in names


def test_axis_candidates_drops_an_author_that_echoes_the_title():
    b = _book(tags=EmbeddedTags(artist="Dune"), authors=["Dune"], title="Dune")
    names = {name for _v, _w, name in axis_candidates(b, "author", 0.0)}
    assert "tag" not in names


def test_axis_candidates_uses_the_folder_name_for_title():
    b = _book(folder="/audio/Frank Herbert/Dune", tags=EmbeddedTags(album="Dune"), title="Dune")
    got = {name: value for value, _weight, name in axis_candidates(b, "title", 0.0)}
    assert got["tag"] == "Dune"
    assert got["folder"] == "Dune"
    assert got["filename"] == "Dune"


def test_axis_support_rises_with_the_number_of_agreeing_sources():
    lone = axis_support([("Frank Herbert", 0.75, "folder")])
    pair = axis_support([("Frank Herbert", 0.75, "folder"), ("Frank Herbert", 0.65, "filename")])
    assert lone < pair
    assert pair == 1.0                      # 0.75 + 0.65 == FULL_SUPPORT


def test_axis_support_counts_only_the_winning_side_of_a_disagreement():
    split = axis_support([("Frank Herbert", 0.75, "folder"), ("Someone Else", 0.65, "filename")])
    assert split == round(0.75 / FULL_SUPPORT, 4)


def test_axis_support_does_not_reward_a_lone_unopposed_source():
    # Regression guard: tally().share is 1.0 for a single voter, so a share-based model would give an
    # uncorroborated tag full credit. Support must come from summed agreeing WEIGHT.
    assert axis_support([("Dune", 0.30, "tag")]) < 0.25


def test_axis_support_is_zero_without_candidates():
    assert axis_support([]) == 0.0
