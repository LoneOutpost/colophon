"""The identity badge reads a score against its own ceiling.

Identity confidence is a score out of what its CLASS of evidence can prove (70 from the library's
own labelling, 95 from an external match, 100 from a confirmation), never out of 100. Judging all
three against one fixed threshold told the user a fully corroborated book was mediocre: on a real
269-book library 238 of 269 badges rendered amber, 218 of them books sitting exactly at their
ceiling with nothing left to prove locally.

The badge is featured before and after a match. A match only adds evidence, so it must never make
the badge read worse: the colour starts at the local ceiling for every class.
"""

from pathlib import Path

from colophon.core.models import BookUnit, Provenance
from colophon.ui.workspace import identity_badge


def _book(score: float, *, matched: bool = False, confirmed: bool = False) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/lib/Frank Herbert/Dune"))
    b.title, b.authors = "Dune", ["Frank Herbert"]
    b.provenance["authors"] = (
        Provenance.HARDCOVER.value if matched else Provenance.DIRECTORY.value)
    b.manually_confirmed = confirmed
    b.identity_confidence = score
    return b


def test_a_locally_maxed_book_reads_positive_not_mediocre():
    label, colour, _ = identity_badge(_book(70))
    assert label == "70/70"
    assert colour == "positive"


def test_a_match_keeps_its_ceiling_in_the_label_but_never_reads_worse():
    """70 out of a possible 95 still says something different from 70 out of 70, so the label keeps
    the ceiling. But the match only added evidence: reading it amber told the user they had lost
    confidence by matching."""
    label, colour, tip = identity_badge(_book(78, matched=True))
    assert label == "78/95"
    assert colour == "positive"
    assert "a source match agrees" in tip


def test_a_confirmed_book_is_scored_out_of_a_hundred():
    label, colour, _ = identity_badge(_book(100, confirmed=True))
    assert label == "100/100"
    assert colour == "positive"


def test_a_badly_evidenced_book_still_reads_negative():
    label, colour, _ = identity_badge(_book(28))
    assert label == "28/70"
    assert colour == "negative"


def test_the_tooltip_says_what_the_score_is_out_of_and_what_to_do():
    _, _, tip = identity_badge(_book(70))
    assert "70 of a possible 70" in tip
    assert "Match it against a source" in tip


def test_a_poor_provider_fit_does_not_replace_the_featured_identity():
    """Real case: The Mediterranean Caper read 95/95 locally-plus-match, then the row swapped to the
    provider's fit score of 53 and turned amber. The fit score lives in the State tab now."""
    book = _book(95, matched=True)
    book.confidence = 53.0
    label, colour, _ = identity_badge(book)
    assert label == "95/95"
    assert colour == "positive"


def test_a_confirmed_book_says_so():
    _, _, tip = identity_badge(_book(100, confirmed=True))
    assert "you confirmed this book" in tip
