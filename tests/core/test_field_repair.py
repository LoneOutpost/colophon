"""repair_fields: safe, source-preserving field cleanings (series-code title affix, bad year)."""
from pathlib import Path

from colophon.core.field_repair import repair_fields
from colophon.core.models import BookUnit, Provenance


def _book(title=None, prov="tag", year=None, authors=None, authors_prov="tag"):
    b = BookUnit.new(source_folder=Path("/x/Book"))
    if title is not None:
        b.title = title
        b.provenance["title"] = prov
    if authors is not None:
        b.authors = authors
        b.provenance["authors"] = authors_prov
    b.publish_year = year
    return b


def test_strips_series_code_affix_from_tag_title():
    b = _book("SB 01 - StarBridge", prov=Provenance.TAG.value)
    assert repair_fields(b) is True
    assert b.title == "Star Bridge"
    assert b.provenance["title"] == Provenance.TAG.value  # source preserved


def test_leaves_manual_title_untouched():
    b = _book("SB 01 - StarBridge", prov=Provenance.MANUAL.value)
    assert repair_fields(b) is False
    assert b.title == "SB 01 - StarBridge"


def test_leaves_plain_title_untouched():
    b = _book("The Way of Kings", prov=Provenance.TAG.value)
    assert repair_fields(b) is False
    assert b.title == "The Way of Kings"


def test_strips_trailing_series_book_affix():
    b = _book("Some Title - Bk01", prov=Provenance.TAG.value)
    assert repair_fields(b) is True
    assert b.title == "Some Title"


def test_strips_both_leading_and_trailing_affixes():
    b = _book("SB 01 - StarBridge - Book 3", prov=Provenance.TAG.value)
    assert repair_fields(b) is True
    assert b.title == "Star Bridge"


def test_clamps_low_year():
    b = _book("T", year=1)
    assert repair_fields(b) is True
    assert b.publish_year is None


def test_clamps_far_future_year():
    b = _book("T", year=3000)
    assert repair_fields(b) is True
    assert b.publish_year is None


def test_keeps_valid_year():
    b = _book("T", year=1982)
    assert repair_fields(b) is False
    assert b.publish_year == 1982


def test_idempotent():
    b = _book("SB 01 - StarBridge", prov=Provenance.TAG.value, year=1)
    assert repair_fields(b) is True
    assert repair_fields(b) is False   # second pass finds nothing
    assert b.title == "Star Bridge" and b.publish_year is None


def test_repair_strips_a_catalog_code_prefix():
    b = _book(title="EV01 Dies the Fire")
    assert repair_fields(b) is True
    assert b.title == "Dies the Fire"


def test_repair_leaves_a_real_title_that_merely_starts_with_letters_and_digits():
    # Must not eat a legitimate title.
    for keep in ("SSN", "1Q84", "Apollo 13", "Se7en", "Fahrenheit 451",
                 "Catch-22", "Slaughterhouse-Five"):   # Catch-22 is why the encode rule needs 3 digits
        b = _book(title=keep)
        repair_fields(b)
        assert b.title == keep, keep


def test_repair_strips_a_narrator_suffix_from_an_author():
    b = _book(title="Family Linen", authors=["Lee Smith--Narr: Linda Stephens"])
    assert repair_fields(b) is True
    assert b.authors == ["Lee Smith"]


def test_repair_strips_trailing_encode_junk_from_a_title():
    for raw, want in (("Sharpe's Gold-237", "Sharpe's Gold"),
                      ("Sharpe's Skirmish-55.9", "Sharpe's Skirmish")):
        b = _book(title=raw)
        assert repair_fields(b) is True
        assert b.title == want
