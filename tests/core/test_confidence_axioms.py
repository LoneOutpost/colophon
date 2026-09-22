from pathlib import Path

from colophon.core.confidence_axioms import (
    CEIL_MANUAL,
    CEIL_MATCH,
    W_FOLDER,
    W_GRAPH,
    W_MANUAL,
    W_MATCH,
    Cap,
    ScoreCtx,
    Support,
    axis_candidates,
    axis_support,
    cf_author_support,
    cf_manual,
    cf_match_ceiling,
    score_identity,
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
    assert source_weight("manual", rich) == W_MANUAL
    assert source_weight("audnexus", rich) == W_MATCH
    assert source_weight("directory", rich) == W_FOLDER
    # a folder outranks even a perfectly complete tag
    assert source_weight("directory", rich) >= source_weight("tag", rich)


def test_source_weight_scales_a_tag_by_its_completeness():
    bare = EmbeddedTags(title="t")
    rich = EmbeddedTags(title="t", album="a", artist="x", narrator="n",
                        series="s", year=1, genre="g", description="d", asin="B")
    assert source_weight("tag", bare) < source_weight("tag", rich)
    assert source_weight("tag", rich) == W_FOLDER          # 0.30 + 0.45 == 0.75


def test_source_weight_treats_every_real_provider_as_a_match():
    # Regression: _STRONG_ID_PROV used the string "match", which Provenance never emits, so a matched
    # field was scored as a folder guess.
    rich = EmbeddedTags()
    for provider in ("audnexus", "audible", "hardcover", "openlibrary", "googlebooks"):
        assert source_weight(provider, rich) == W_MATCH, provider


def test_source_weight_gives_a_graph_resolved_value_a_flat_weight():
    # Deliberately NOT the classifying node's kind_confidence: that reflects how much of the tree the
    # classifier had just examined, so a scoped re-derive and a whole-root re-derive would score the
    # same book differently. Scan scope is an artifact, not evidence about the book.
    assert source_weight("graphing", EmbeddedTags()) == W_GRAPH
    # and it equals the folder weight: both are the directory structure, resolved with different
    # precision, so whichever one a given derivation path reaches must score the same.
    assert W_GRAPH == W_FOLDER


def test_source_weight_gives_an_unknown_provenance_no_vote():
    assert source_weight(None, EmbeddedTags()) == 0.0
    assert source_weight("something-new", EmbeddedTags()) == 0.0


def _book(folder="/audio/Frank Herbert/Dune", stem="Dune", prov="tag", **kw):
    """A realistic book. `prov` records where the committed title/author came from, as every real
    book does: `reconcile` always stamps provenance, and the scoring reads it rather than re-parsing
    the folder itself."""
    b = BookUnit.new(source_folder=Path(folder))
    b.source_files = [SourceFile(path=Path(folder) / f"{stem}.mp3", size=1, duration_seconds=1.0,
                                 ext="mp3", tags=kw.pop("tags", EmbeddedTags()))]
    for k, v in kw.items():
        setattr(b, k, v)
    if prov:
        if b.authors:
            b.provenance["authors"] = prov
        if b.title:
            b.provenance["title"] = prov
        if b.series:
            b.provenance["series"] = prov
    return b


def test_axis_candidates_collects_one_vote_per_source():
    b = _book(tags=EmbeddedTags(artist="Frank Herbert"), authors=["Frank Herbert"])
    got = {name: value for value, _weight, name in axis_candidates(b, "author")}
    assert got["tag"] == "Frank Herbert"
    assert got["folder"] == "Frank Herbert"      # the parent directory of the book folder


def test_axis_candidates_drops_a_junk_author_so_it_cannot_vote():
    b = _book(tags=EmbeddedTags(artist="Narrated by William Gaminara"),
              authors=["Narrated by William Gaminara"])
    names = {name for _v, _w, name in axis_candidates(b, "author")}
    assert "tag" not in names


def test_axis_candidates_drops_an_author_that_echoes_the_title():
    b = _book(tags=EmbeddedTags(artist="Dune"), authors=["Dune"], title="Dune")
    names = {name for _v, _w, name in axis_candidates(b, "author")}
    assert "tag" not in names


def test_axis_candidates_uses_the_folder_name_for_title():
    b = _book(folder="/audio/Frank Herbert/Dune", tags=EmbeddedTags(album="Dune"), title="Dune")
    got = {name: value for value, _weight, name in axis_candidates(b, "title")}
    assert got["tag"] == "Dune"
    assert got["folder"] == "Dune"
    assert got["filename"] == "Dune"


def test_axis_support_rises_with_the_number_of_agreeing_sources():
    lone = axis_support([("Frank Herbert", 0.75, "folder")], "Frank Herbert")
    pair = axis_support([("Frank Herbert", 0.75, "folder"), ("Frank Herbert", 0.65, "filename")],
                        "Frank Herbert")
    assert lone < pair
    assert pair == 1.0                      # 0.75 + 0.65 == FULL_SUPPORT


def test_axis_support_counts_only_the_sources_that_back_the_committed_value():
    backed_by_one = axis_support([("Frank Herbert", 0.75, "folder")], "Frank Herbert")
    contested = axis_support([("Frank Herbert", 0.75, "folder"), ("Someone Else", 0.65, "filename")],
                             "Frank Herbert")
    # A source naming someone else is evidence AGAINST, not merely absent support, so a contested
    # value scores below the same value uncontested.
    assert 0 < contested < backed_by_one


def test_axis_support_is_starved_when_the_sources_contradict_what_was_committed():
    # Confidence measures how well THIS identity is evidenced. A book whose folder and filename both
    # name someone else must score low, not inherit the dissenters' agreement with each other.
    contradicted = axis_support(
        [("Someone Else", 0.75, "folder"), ("Someone Else", 0.65, "filename")], "Frank Herbert")
    assert contradicted == 0.0


def test_axis_support_does_not_reward_a_lone_unopposed_source():
    # Regression guard: tally().share is 1.0 for a single voter, so a share-based model would give an
    # uncorroborated tag full credit. Support must come from summed agreeing WEIGHT. Asserted as an
    # invariant rather than a fixed number, because FULL_SUPPORT is tuned against real libraries.
    lone_bare_tag = axis_support([("Dune", 0.30, "tag")], "Dune")
    corroborated = axis_support([("Dune", 0.75, "folder"), ("Dune", 0.65, "filename")], "Dune")
    assert lone_bare_tag < 0.5, "a lone bare tag is not half-way to full support"
    assert lone_bare_tag < corroborated == 1.0


def test_axis_support_is_zero_without_candidates_or_without_a_committed_value():
    assert axis_support([], "Dune") == 0.0
    assert axis_support([("Dune", 0.75, "folder")], None) == 0.0


def test_cf_author_support_emits_one_support_with_a_reason():
    b = _book(tags=EmbeddedTags(artist="Frank Herbert"), authors=["Frank Herbert"])
    out = cf_author_support(b, ScoreCtx())
    assert len(out) == 1
    assert isinstance(out[0], Support) and out[0].axis == "author"
    assert out[0].weight > 0
    assert "folder" in out[0].reason or "tag" in out[0].reason


def test_cf_author_support_is_silent_without_an_author():
    assert cf_author_support(_book(), ScoreCtx()) == []


def test_cf_match_ceiling_grants_the_match_ceiling_only_with_a_provider_provenance():
    b = _book(title="Dune", authors=["Frank Herbert"])
    assert cf_match_ceiling(b, ScoreCtx()) == []
    b.provenance = {"title": "audnexus"}
    out = cf_match_ceiling(b, ScoreCtx())
    assert out == [Cap(CEIL_MATCH, out[0].reason)] and out[0].ceiling == CEIL_MATCH


def test_cf_manual_grants_both_the_ceiling_and_full_support():
    b = _book()
    assert cf_manual(b, ScoreCtx()) == []
    b.manually_confirmed = True
    out = cf_manual(b, ScoreCtx())
    caps = [c for c in out if isinstance(c, Cap)]
    supports = [c for c in out if isinstance(c, Support)]
    assert caps[0].ceiling == CEIL_MANUAL
    assert {s.axis for s in supports} == {"author", "title"}


def test_local_evidence_cannot_exceed_the_local_ceiling():
    # Everything agreeing, richly tagged: still capped, because no evidence is independent of the
    # library's own labelling.
    rich = EmbeddedTags(title="Dune", album="Dune", artist="Frank Herbert", narrator="n",
                        series="Dune", year=1965, genre="g", description="d", asin="B")
    b = _book(folder="/audio/Frank Herbert/Dune", stem="Dune", tags=rich,
              title="Dune", authors=["Frank Herbert"])
    assert score_identity(b, ScoreCtx()).score == 70


def test_an_uncorroborated_tag_scores_far_below_a_corroborated_one():
    rich = EmbeddedTags(title="Dune", album="Dune", artist="Frank Herbert", narrator="n",
                        series="Dune", year=1965, genre="g", description="d", asin="B")
    corroborated = _book(folder="/audio/Frank Herbert/Dune", stem="Dune", tags=rich,
                         title="Dune", authors=["Frank Herbert"])
    alone = _book(folder="/audio/Assorted/Disc 1", stem="track01", tags=rich,
                  title="Dune", authors=["Frank Herbert"])
    assert score_identity(alone, ScoreCtx()).score < score_identity(corroborated, ScoreCtx()).score


def test_a_narrator_as_author_costs_the_author_axis_its_strongest_vote():
    # The narrator credit casts no author vote, so the axis keeps only whatever the folder supplies.
    # Assert the DROP rather than an absolute number: the absolute depends on what else happens to
    # vote, which is a tuning question, not a behavioural one.
    narrator = _book(folder="/audio/Assorted/Sharpe", stem="01 - Sharpes Siege",
                     tags=EmbeddedTags(artist="Narrated by William Gaminara", album="Sharpes Siege"),
                     title="Sharpe's Siege", authors=["Narrated by William Gaminara"])
    real = _book(folder="/audio/Bernard Cornwell/Sharpes Siege", stem="01 - Sharpes Siege",
                 tags=EmbeddedTags(artist="Bernard Cornwell", album="Sharpes Siege"),
                 title="Sharpe's Siege", authors=["Bernard Cornwell"])
    assert score_identity(narrator, ScoreCtx()).score < score_identity(real, ScoreCtx()).score


def test_manual_confirmation_reaches_full_score():
    b = _book(folder="/audio/Frank Herbert/Dune", stem="Dune",
              tags=EmbeddedTags(artist="Frank Herbert", album="Dune"),
              title="Dune", authors=["Frank Herbert"])
    b.manually_confirmed = True
    assert score_identity(b, ScoreCtx()).score == 100


def test_every_contribution_is_explained_in_the_signals():
    b = _book(tags=EmbeddedTags(artist="Frank Herbert"), title="Dune", authors=["Frank Herbert"])
    result = score_identity(b, ScoreCtx())
    assert result.signals
    assert all(s.detail for s in result.signals)
    assert any("author" in s.detail for s in result.signals)


def test_agreement_survives_a_stripped_apostrophe():
    # normalize_key turns an apostrophe into a space, so "Sharpe's Siege" and the apostrophe-stripped
    # "Sharpes Siege" that taggers and filesystems routinely produce would read as a contradiction.
    # Titles with apostrophes are common enough to depress a whole library's scores.
    agreeing = axis_support([("Sharpes Siege", 0.75, "folder"),
                             ("Sharpe's Siege", 0.65, "filename")], "Sharpe's Siege")
    assert agreeing == 1.0
    # a genuinely different title still fails to agree
    assert axis_support([("Sharpes Honour", 0.75, "folder")], "Sharpe's Siege") == 0.0


def test_a_peripheral_field_match_does_not_lift_the_ceiling():
    # Measured on a real library: keying the raised ceiling on ANY matched field gave it to 100 of
    # 167 books whose only match was genres/tags/subtitle. A provider filling in genres has not
    # corroborated who wrote the book.
    b = _book(title="Dune", authors=["Frank Herbert"])
    b.provenance = {"genres": "audnexus", "subtitle": "audnexus", "tags": "audnexus"}
    assert cf_match_ceiling(b, ScoreCtx()) == []
    b.provenance = {"genres": "audnexus", "authors": "audnexus"}
    assert cf_match_ceiling(b, ScoreCtx())[0].ceiling == CEIL_MATCH


def _ceiling_the_engine_applied(book) -> float:
    """The ceiling `score_identity` actually used, read back off the signal it records."""
    signals = score_identity(book, ScoreCtx()).signals
    return next(s.points for s in signals if s.name == "ceiling_applied") / 100


def test_identity_ceiling_matches_the_engine(tmp_path):
    """`identity_ceiling` is a second statement of a rule the engine already owns, so the two are
    pinned together: the UI reads the standalone one to say what a score is out of, and a drift
    between them would misreport every book."""
    from colophon.core.confidence_axioms import identity_ceiling
    from colophon.core.models import Provenance

    for confirmed in (False, True):
        for matched in (False, True):
            book = BookUnit.new(source_folder=tmp_path / "Dune")
            book.title, book.authors = "Dune", ["Frank Herbert"]
            book.provenance["authors"] = (
                Provenance.HARDCOVER.value if matched else Provenance.DIRECTORY.value)
            book.manually_confirmed = confirmed
            assert identity_ceiling(book) == _ceiling_the_engine_applied(book), (
                f"confirmed={confirmed} matched={matched}")


def test_identity_ceiling_ignores_a_match_on_a_peripheral_field(tmp_path):
    """A provider filling in genres has not verified who wrote the book, so it must not lift the
    ceiling. Same rule `cf_match_ceiling` enforces, checked through the standalone reader."""
    from colophon.core.confidence_axioms import CEIL_LOCAL, identity_ceiling
    from colophon.core.models import Provenance

    book = BookUnit.new(source_folder=tmp_path / "Dune")
    book.title, book.authors = "Dune", ["Frank Herbert"]
    book.provenance["authors"] = Provenance.DIRECTORY.value
    book.provenance["genres"] = Provenance.HARDCOVER.value
    assert identity_ceiling(book) == CEIL_LOCAL
