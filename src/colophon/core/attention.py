"""The Attention view model: turn a book + its active findings into ordered, actionable items.

Pure and UI-agnostic so both Attention surfaces (the book-detail pane and the At-a-Glance tab)
render from one source and cannot drift apart. Each item carries the guidance suggestion and the
next-actions that resolve it; a wholly-missing book becomes a code-less delete item."""

from __future__ import annotations

from typing import NamedTuple

from colophon.core.guidance import FOLDER_AUTHOR_FIELD, FixAction, finding_guidance
from colophon.core.models import BookUnit, Finding, FindingCode, FindingSeverity
from colophon.core.textlist import join_list


class AttentionItem(NamedTuple):
    severity: FindingSeverity
    detail: str
    suggestion: str
    actions: tuple[FixAction, ...]
    code: FindingCode | None  # None for the synthetic missing-book item
    key: str | None = None    # the finding's acknowledgement identity; None when there is no finding
    suggested: str | None = None  # the folder's value USE_SUGGESTED writes, or the folder name
                                  # USE_FOLDER_NAME makes the author (conflict findings only)
    field: str | None = None      # the book field USE_SUGGESTED writes it to
    source: str | None = None     # where the disputed value came from ("album tag", "tag", "title")


def _book_holds(book: BookUnit, field: str, value: str) -> bool:
    """Whether the book's `field` already reads `value`, ignoring case and outer spacing."""
    held = join_list(book.authors) if field == "authors" else getattr(book, field, None)
    return (held or "").strip().casefold() == value.strip().casefold()


def attention_items(book: BookUnit, active_findings: list[Finding]) -> list[AttentionItem]:
    """The Attention items for `book`. `active_findings` is the caller's already-filtered list
    (acknowledged and suppressed removed). A missing book yields one code-less delete item; each
    finding yields an item from its guidance."""
    if book.missing:
        return [AttentionItem(
            severity=FindingSeverity.ERROR,
            detail="The book's files are missing from disk.",
            suggestion="Restore the folder and rescan, or delete this book from Colophon.",
            actions=(FixAction.DELETE,),
            code=None,
        )]
    items: list[AttentionItem] = []
    for f in active_findings:
        g = finding_guidance(f.code)
        suggestion, actions = g.suggestion, g.actions
        if f.field == FOLDER_AUTHOR_FIELD and f.suggested:
            # The fix reclassifies the author folder, so every book under it follows; it never
            # writes this one book's field, which is why it is not USE_SUGGESTED.
            actions = (*(a for a in actions if a is not FixAction.ACKNOWLEDGE),
                       FixAction.USE_FOLDER_NAME, FixAction.ACKNOWLEDGE)
        elif f.field in ("title", "authors") and f.suggested:
            if _book_holds(book, f.field, f.suggested):
                # The book already reads the folder's value; only the file's tag still disagrees,
                # so there is nothing to apply and writing the tags is the fix.
                suggestion = f"{suggestion} Write tags to fix the file."
                actions = (FixAction.ACKNOWLEDGE,)
            else:
                # Offered just before Acknowledge: the fix first, dismissing it last.
                actions = (*(a for a in actions if a is not FixAction.ACKNOWLEDGE),
                           FixAction.USE_SUGGESTED, FixAction.ACKNOWLEDGE)
        items.append(AttentionItem(
            severity=f.severity, detail=f.detail, suggestion=suggestion,
            actions=actions, code=f.code, key=f.key,
            suggested=f.suggested, field=f.field, source=f.source,
        ))
    return items
