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


def _conflict(**kw) -> Finding:
    base = dict(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                detail='folder "Porterhouse Blue" vs title "Ph1" (from the album tag)',
                field="title", current="Ph1", suggested="Porterhouse Blue", source="album tag")
    return Finding(**{**base, **kw})


def test_conflict_offers_the_folders_value_before_acknowledge():
    [item] = attention_items(_book(title="Ph1"), [_conflict()])
    assert FixAction.USE_SUGGESTED in item.actions
    assert item.actions.index(FixAction.USE_SUGGESTED) < item.actions.index(FixAction.ACKNOWLEDGE)
    assert (item.field, item.suggested, item.source) == ("title", "Porterhouse Blue", "album tag")


def test_conflict_already_fixed_in_the_book_offers_acknowledge_and_write_tags():
    # The book already holds the folder's value (case and spacing aside); only the file's tag
    # disagrees, so there is nothing to apply and Write tags is what fixes it.
    [item] = attention_items(_book(title=" porterhouse blue"), [_conflict()])
    assert item.actions == (FixAction.ACKNOWLEDGE,)
    assert "Write tags to fix the file." in item.suggestion


def test_author_conflict_compares_against_the_joined_authors():
    f = _conflict(detail="author: tag 'A' vs folder 'Tom Sharpe'", field="authors",
                  current="A", suggested="Tom Sharpe", source="tag")
    assert FixAction.USE_SUGGESTED in attention_items(_book(authors=["A"]), [f])[0].actions
    assert FixAction.USE_SUGGESTED not in attention_items(
        _book(authors=["Tom Sharpe"]), [f])[0].actions


def test_unstructured_conflict_offers_no_value():
    f = _conflict(field=None, current=None, suggested=None, source=None)
    [item] = attention_items(_book(title="Ph1"), [f])
    assert FixAction.USE_SUGGESTED not in item.actions
    assert FixAction.ACKNOWLEDGE in item.actions
