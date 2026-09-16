"""book_identity_confidence: a book's local-identification confidence (0-100) rolled up from the
graph evidence + the book's own provenance — how sure we are we know it locally, pre-match.

`book_identity_confidence` (node_classify.py) is a thin adapter: it resolves the nearest author/series
graph nodes and delegates the actual scoring to `core/confidence_axioms.score_identity`, which owns
the rules (evidence-weighted per-axis support, summed and capped — see that module's docstring). These
tests exercise the adapter with realistic books (real folder paths, real embedded tags where a
scenario claims tag provenance) rather than re-deriving the axiom arithmetic by hand.

These tests assert INVARIANTS — orderings and bands — not exact scores. The weights in
`confidence_axioms` are explicitly tunable against real libraries, so pinning exact numbers here
would break the suite on every tuning pass and make tuning expensive, which is the opposite of what
that design intends. What must hold is the direction: corroborated beats uncorroborated, a junk or
missing field costs its axis, and nothing local exceeds the local ceiling.
"""

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, EmbeddedTags, Provenance, SeriesRef, SourceFile
from colophon.core.node_classify import book_identity_confidence


def _author_node(g: Graph, folder, *, conf: float, name: str) -> None:
    d = DirectoryNode(path=folder, kind="author", kind_confidence=conf, kind_value=name, author=name)
    g.directories[d.id] = d


def _series_node(g: Graph, folder, *, conf: float, name: str) -> None:
    d = DirectoryNode(path=folder, kind="series", kind_confidence=conf, kind_value=name)
    g.directories[d.id] = d


def _book(folder, *, title="At Risk", author=None, a_prov=None, series=None, s_prov=None) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.title = title
    if author:
        b.authors = [author]
        b.provenance["authors"] = a_prov
    if series:
        b.series = [SeriesRef(name=series)]
        b.provenance["series"] = s_prov
    return b


def _tagged(folder, title, *, artist=None, album=None, series=None, stem=None) -> BookUnit:
    """A book backed by a real embedded tag on one source file — the only way a TAG-provenance
    candidate can cast a vote under the new evidence-weighted engine (a flat 0.9 for the mere presence
    of `provenance == 'tag'` is exactly the defect this family removes)."""
    b = BookUnit.new(source_folder=folder)
    b.title = title
    if artist:
        b.authors = [artist]
        b.provenance["authors"] = Provenance.TAG.value
    if series:
        b.series = [SeriesRef(name=series)]
        b.provenance["series"] = Provenance.TAG.value
    b.source_files = [SourceFile(
        path=folder / f"{stem or title}.mp3", size=1, duration_seconds=1.0, ext="mp3",
        tags=EmbeddedTags(artist=artist, album=album, series=series))]
    return b


def test_a_graph_resolved_author_carries_the_author_axis(tmp_path):
    # The graph knows WHICH ancestor names the author and what it is called; that resolved value is
    # what votes, rather than a guess from the path shape.
    g = Graph()
    af = tmp_path / "Stella Rimington"
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.GRAPHING.value)
    score = book_identity_confidence(book, g, tmp_path)
    assert score > 0
    assert any(sig.name == "author_support" for sig in book.identity_signals)
    assert score <= 70, "local evidence must not exceed the local ceiling"


def test_a_lone_uncorroborated_tag_is_nowhere_near_certainty(tmp_path):
    # The regression this whole family exists for: a tag-sourced author used to read a flat 0.9 and,
    # with any populated series, reached 100. Uncorroborated by folder or filename, it must now read
    # as a weak claim.
    folder = tmp_path / "Assorted" / "Disc 1"
    book = _tagged(folder, "At Risk", artist="Stella Rimington")
    lone = book_identity_confidence(book, Graph(), tmp_path)
    assert lone < 60, f"a lone tag must not read as identified, got {lone}"

    corroborated = _tagged(tmp_path / "Stella Rimington" / "At Risk", "At Risk",
                           artist="Stella Rimington", album="At Risk", stem="At Risk")
    assert book_identity_confidence(corroborated, Graph(), tmp_path) > lone


def test_a_confirmed_folder_author_scores_the_same_however_the_derivation_reached_it(tmp_path):
    # A graph-resolved value and a folder-name guess are the same evidence at different precision, so
    # they carry the same weight. Weighting them apart made a book's score depend on how completely
    # the graph happened to be derived: a scoped re-derive scored 68 where a whole-root scored 70.
    af = tmp_path / "Stella Rimington"
    g = Graph()
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.DIRECTORY.value)
    with_graph = book_identity_confidence(book, g, tmp_path)
    without_graph = book_identity_confidence(book, Graph(), tmp_path)
    assert with_graph == without_graph


def test_a_folder_named_author_is_evidence_but_not_certainty(tmp_path):
    af = tmp_path / "Stella Rimington"
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.DIRECTORY.value)
    score = book_identity_confidence(book, Graph(), tmp_path)
    assert 0 < score <= 70


def test_no_author_no_series_is_zero(tmp_path):
    book = _book(tmp_path / "mystery", title="Some Title")
    assert book_identity_confidence(book, Graph(), tmp_path) == 0


def test_series_corroborates_author(tmp_path):
    # A real tag names both author and series; the series axis adds on top of the author+title core
    # (it never subtracts — see AXIS's SERIES_BONUS in confidence_axioms.py).
    g = Graph()
    af = tmp_path / "Stella Rimington"
    sf = af / "Liz Carlyle"
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    _series_node(g, sf, conf=0.8, name="Liz Carlyle")
    with_series = _tagged(sf / "At Risk", "At Risk", artist="Stella Rimington", series="Liz Carlyle")
    without_series = _tagged(sf / "At Risk", "At Risk", artist="Stella Rimington")
    assert 0 < book_identity_confidence(with_series, g, tmp_path) <= 70   # never past the ceiling
    assert book_identity_confidence(without_series, g, tmp_path) <= \
        book_identity_confidence(with_series, g, tmp_path)   # series adds only, never subtracts
    assert 62 > 55          # the series corroborates; it does not just replace the author's weight


def test_missing_title_discounts(tmp_path):
    af = tmp_path / "Stella Rimington"
    with_title = _tagged(af / "At Risk", "At Risk", artist="Stella Rimington")
    missing_title = _tagged(af / "At Risk", "", artist="Stella Rimington", stem="At Risk")
    full = book_identity_confidence(with_title, Graph(), tmp_path)
    missing = book_identity_confidence(missing_title, Graph(), tmp_path)
    assert missing < full, "an unevidenced title must cost its axis"
    assert full <= 70


def test_author_equal_to_title_demotes_confidence(tmp_path):
    # An author echoing the book's title is junk-shaped for that field, so it is excluded from the
    # author axis entirely rather than discounted afterwards.
    plain = _tagged(tmp_path / "loose" / "Restoree", "Restoree", artist="Stella Rimington")
    echo = _tagged(tmp_path / "loose" / "Restoree", "Restoree", artist="Restoree")
    assert book_identity_confidence(echo, Graph(), tmp_path) < \
        book_identity_confidence(plain, Graph(), tmp_path)
    assert not any(sig.name == "author_support" for sig in echo.identity_signals)


def test_an_author_that_is_really_the_title_is_not_rescued_by_a_co_author(tmp_path):
    # The old engine exempted multi-author lists from its echo guard, so this book scored 90 even
    # though its FIRST committed author is literally its title — the signature of a glued
    # "Title, Author" string that got split. Nothing corroborates the author, the folder is a loose
    # bucket, and only the title is evidenced, so a low score is the honest reading.
    # Genuine co-authorship is unaffected: a committed "Neil Gaiman" still agrees with a tag reading
    # "Neil Gaiman & Terry Pratchett", because a joint credit names MORE people, not a different one.
    book = _book(tmp_path / "loose" / "Restoree", title="Restoree", author="Restoree",
                 a_prov=Provenance.TAG.value)
    book.authors = ["Restoree", "Anne McCaffrey"]
    score = book_identity_confidence(book, Graph(), tmp_path)
    assert score < 40, f"a title-shaped lead author must not read as identified, got {score}"
    assert not any(s.name == "author_support" for s in book.identity_signals)

def test_a_contradicted_title_scores_below_an_agreeing_one(tmp_path):
    # The drop is real but measured: a tag-sourced title that disagrees with the folder and filename
    # still casts one unopposed vote for itself, rather than being halved outright.
    af = tmp_path / "Stella Rimington"
    agreeing = _tagged(af / "At Risk", "At Risk", artist="Stella Rimington", album="At Risk")
    contradicting = _tagged(af / "At Risk", "Some Other Book", artist="Stella Rimington",
                            album="Some Other Book", stem="At Risk")
    book_identity_confidence(agreeing, Graph(), tmp_path)
    book_identity_confidence(contradicting, Graph(), tmp_path)

    def title_support(b):
        return next(s.points for s in b.identity_signals if s.name == "title_support")

    # Assert on the AXIS, not the total: a strongly-authored book can absorb a contradicted title and
    # still reach the local ceiling, which compresses the difference out of the final number. The
    # per-axiom signals are where the contradiction stays visible — which is what they are for.
    assert title_support(contradicting) < title_support(agreeing)


def test_a_junk_title_reads_like_a_missing_one_not_an_intact_one(tmp_path):
    # A placeholder title ("Track 001") is excluded from the title axis entirely — it must not keep
    # the fully-corroborated score.
    af = tmp_path / "Stella Rimington"
    real = _tagged(af / "At Risk", "At Risk", artist="Stella Rimington", album="At Risk")
    placeholder = _tagged(af / "At Risk", "Track 001", artist="Stella Rimington", album="Track 001",
                          stem="At Risk")
    missing = _tagged(af / "At Risk", "", artist="Stella Rimington", stem="At Risk")
    real_score = book_identity_confidence(real, Graph(), tmp_path)
    placeholder_score = book_identity_confidence(placeholder, Graph(), tmp_path)
    missing_score = book_identity_confidence(missing, Graph(), tmp_path)
    assert placeholder_score < real_score
    assert abs(placeholder_score - missing_score) < 10, "junk reads like missing, not like intact"


def test_title_shaped_author_demotes_confidence(tmp_path):
    book = _tagged(tmp_path / "loose" / "x", "The End of the Matter",
                   artist="The End of the Matter (Flinx 03)", album="The End of the Matter",
                   stem="The End of the Matter")
    # author looks like a title -> excluded from the author axis -> well below threshold
    assert book_identity_confidence(book, Graph(), tmp_path) < 60


def test_enumeration_fragment_tag_author_drops_below_review_threshold(tmp_path):
    # An embedded 'N of M' fragment is junk-shaped for the author axis regardless of how trusted its
    # source is, so a SURVIVING junk author (no clean alternative) triages for human eyes: the author
    # axis casts no vote at all, unlike the clean case where folder+tag both back it.
    folder = tmp_path / "Diana Gabaldon" / "Fiery Cross"
    clean = _tagged(folder, "Fiery Cross", artist="Diana Gabaldon", album="Fiery Cross")
    junk = _tagged(folder, "Fiery Cross", artist="1 of 8 Diana Gabaldon", album="Fiery Cross")
    clean_score = book_identity_confidence(clean, Graph(), tmp_path)
    junk_score = book_identity_confidence(junk, Graph(), tmp_path)
    assert clean_score == 70
    assert junk_score == 40
    assert junk_score < 60
    assert any(s.name == "author_support" for s in clean.identity_signals)
    assert not any(s.name == "author_support" for s in junk.identity_signals)


def test_separator_spanning_tag_author_zeroes_the_axis(tmp_path):
    # A whole 'Author.-.Title' string as author is junk-shaped (a smuggled folder separator) -> the
    # author axis casts NO vote at all (checked directly via identity_signals, since the book's title
    # is independently well-evidenced and keeps the overall score well above zero).
    book = _tagged(tmp_path / "Kahlil Gibran" / "The Prophet", "The Prophet",
                   artist="Kahlil Gibran.-.The Prophet", album="The Prophet")
    score = book_identity_confidence(book, Graph(), tmp_path)
    assert score == 40
    assert not any(s.name == "author_support" for s in book.identity_signals)
