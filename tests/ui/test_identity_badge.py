"""The identity badge reads a score against its own ceiling.

Identity confidence is a score out of what its CLASS of evidence can prove (70 from the library's
own labelling, 95 from an external match, 100 from a confirmation), never out of 100. Judging all
three against one fixed threshold told the user a fully corroborated book was mediocre: on a real
269-book library 238 of 269 badges rendered amber, 218 of them books sitting exactly at their
ceiling with nothing left to prove locally.
"""

from pathlib import Path

from colophon.core.models import BookUnit, Provenance
from colophon.ui.workspace import _primary_confidence, identity_badge


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


def test_the_same_score_under_a_higher_ceiling_is_not_maxed():
    """70 out of a possible 70 and 70 out of a possible 95 are different claims. Before, both read
    as a bare '70' against one fixed threshold, which is what made the number uncomparable."""
    label, colour, _ = identity_badge(_book(70, matched=True))
    assert label == "70/95"
    assert colour == "warning"


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


def test_match_confidence_keeps_a_bare_number_and_its_own_threshold():
    """Post-match confidence IS a real 0-100 scale gated on a real threshold, so it must not grow a
    denominator or get judged against an identity ceiling."""
    book = _book(70, matched=True)
    book.confidence = 53.0
    label, colour, _ = _primary_confidence(book, 75.0)
    assert label == "53"
    assert colour == "warning"
