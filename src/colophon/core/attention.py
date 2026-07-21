"""The Attention view model: turn a book + its active findings into ordered, actionable items.

Pure and UI-agnostic so both Attention surfaces (the book-detail pane and the At-a-Glance tab)
render from one source and cannot drift apart. Each item carries the guidance suggestion and the
next-actions that resolve it; a wholly-missing book becomes a code-less delete item."""

from __future__ import annotations

from typing import NamedTuple

from colophon.core.guidance import FixAction, finding_guidance
from colophon.core.models import BookUnit, Finding, FindingCode, FindingSeverity


class AttentionItem(NamedTuple):
    severity: FindingSeverity
    detail: str
    suggestion: str
    actions: tuple[FixAction, ...]
    code: FindingCode | None  # None for the synthetic missing-book item


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
        items.append(AttentionItem(
            severity=f.severity, detail=f.detail, suggestion=g.suggestion,
            actions=g.actions, code=f.code,
        ))
    return items
