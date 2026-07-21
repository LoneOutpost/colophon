from colophon.core.attention import attention_items
from colophon.core.guidance import FixAction
from colophon.core.models import BookUnit, Finding, FindingCode, FindingSeverity


def _book(**kw) -> BookUnit:
    book = BookUnit.new(source_folder=__import__("pathlib").Path("/lib/A/B"))
    for k, v in kw.items():
        setattr(book, k, v)
    return book


def test_advisory_finding_becomes_item_with_acknowledge():
    f = Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR, detail="two works")
    items = attention_items(_book(), [f])
    assert len(items) == 1
    assert items[0].code is FindingCode.MIXED_WORKS
    assert FixAction.ACKNOWLEDGE in items[0].actions


def test_empty_audio_finding_offers_delete():
    f = Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="corrupt")
    items = attention_items(_book(), [f])
    assert FixAction.DELETE in items[0].actions


def test_missing_book_becomes_delete_item_with_no_code():
    book = _book()
    book.missing = True
    items = attention_items(book, [])
    assert len(items) == 1
    assert items[0].code is None
    assert items[0].actions == (FixAction.DELETE,)
    assert "missing" in items[0].detail.lower()
