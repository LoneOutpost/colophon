"""repair_fields: safe, source-preserving field cleanings (series-code title affix, bad year)."""
from pathlib import Path

from colophon.core.field_repair import repair_fields
from colophon.core.models import BookUnit, Provenance


def _book(title=None, prov="tag", year=None):
    b = BookUnit.new(source_folder=Path("/x/Book"))
    if title is not None:
        b.title = title
        b.provenance["title"] = prov
    b.publish_year = year
    return b


def test_strips_series_code_affix_from_tag_title():
    b = _book("SB 01 - StarBridge", prov=Provenance.TAG.value)
    assert repair_fields(b) is True
    assert b.title == "StarBridge"
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
    assert b.title == "StarBridge"


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
    assert b.title == "StarBridge" and b.publish_year is None
