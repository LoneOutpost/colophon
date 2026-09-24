"""The review queue: the books that need a person, grouped by what probably caused each problem.

Derived, never stored: computed from the books and the maintained graph each time the Library
renders, so any fix anywhere (a folder confirmed, a finding acknowledged, a match applied) shows on
the next repaint with nothing to keep in sync.

A book is queued for a reason (blocked, unsure, an open finding, a weak identity) and each reason
names a probable cause: a folder, or the book itself. Books sharing a cause form one group, so a
misclassified author folder reads as one item to fix at the folder instead of N books to fix one by
one; that is also what shows where the automatic identifier errs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from colophon.core.guidance import finding_phrase, finding_scope
from colophon.core.models import (
    BLOCKING_FINDINGS,
    BookState,
    BookUnit,
    Finding,
    active_findings,
)
from colophon.core.triage import confidence_bucket, has_blocking_error, weak_identity_reason

ReasonKind = Literal["blocked", "unsure", "finding", "weak"]
KindOf = Callable[[Path], str]   # a folder's classified kind in the maintained graph, "" when none

_ENTITY_KINDS = frozenset({"author", "series", "franchise"})
# READY is deliberately absent: a Ready book (whether by a confirmed match or Mark ready) can still
# carry an open finding, and Mark ready does not acknowledge findings, so it stays queued until one
# does. Only a genuinely finished or skipped book is settled.
_SETTLED_STATES = frozenset({BookState.SKIPPED, BookState.ORGANIZED, BookState.ENCODED})
_FOLDER_PROVENANCE = frozenset({"directory", "graphing"})


@dataclass(frozen=True)
class Cause:
    """What probably produced a problem: a folder (shared by its books) or one book."""

    kind: Literal["folder", "book"]
    path: Path
    book_id: str | None = None   # set for a book cause: clustered books share a source folder


@dataclass(frozen=True)
class QueueReason:
    kind: ReasonKind
    phrase: str                  # the problem in plain words; part of the grouping key
    cause: Cause


@dataclass
class QueueGroup:
    kind: ReasonKind
    phrase: str
    cause: Cause
    books: list[BookUnit] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.cause.kind == "folder" and len(self.books) > 1:
            n = len(self.books)
            return f"{n} books under {self.cause.path.name}: {self.phrase}"
        book = self.books[0]
        return f"{book.title or book.source_folder.name}: {self.phrase}"


@dataclass
class Queue:
    groups: list[QueueGroup]
    book_count: int              # distinct books; a book with two causes counts once


def nearest_classified(folder: Path, root: Path, kind_of: KindOf,
                       kinds: Iterable[str]) -> Path | None:
    """The nearest folder at or above `folder` (up to and including `root`) classified as one of
    `kinds`, or None."""
    wanted = frozenset(kinds)
    cur = folder
    while True:
        if kind_of(cur) in wanted:
            return cur
        if cur == root or root not in cur.parents:
            return None
        cur = cur.parent


def _book_cause(book: BookUnit) -> Cause:
    return Cause("book", book.source_folder, book_id=book.id)


def _folder_cause(book: BookUnit, root: Path, kind_of: KindOf, kinds: Iterable[str]) -> Cause | None:
    found = nearest_classified(book.source_folder, root, kind_of, kinds)
    return Cause("folder", found) if found is not None else None


def _missing_fallback_cause(book: BookUnit, root: Path) -> Cause:
    """A missing book's parent folder, when no entity ancestor is classified — clamped to the scan
    root, since a book AT the root has no parent to blame that is still inside the library."""
    p = book.source_folder.parent
    if root == book.source_folder or (root != p and root not in p.parents):
        return Cause("folder", root)
    return Cause("folder", p)


def _finding_reason(book: BookUnit, finding: Finding, root: Path, kind_of: KindOf) -> QueueReason:
    scope = finding_scope(finding)
    phrase = finding_phrase(finding)
    if scope == "author_folder":
        cause = _folder_cause(book, root, kind_of, {"author"}) or _book_cause(book)
    elif scope == "own_folder":
        cause = Cause("folder", book.source_folder)
    else:
        cause = _book_cause(book)
    return QueueReason("finding", phrase, cause)


def _weak_reason(book: BookUnit, root: Path, kind_of: KindOf) -> QueueReason:
    weak = weak_identity_reason(book)
    if weak is not None:
        field_name, provenance = weak
        if provenance in _FOLDER_PROVENANCE:
            cause = _folder_cause(book, root, kind_of, {field_name})
            if cause is not None:
                return QueueReason("weak", f"{field_name} only from the folder", cause)
        return QueueReason("weak", f"{field_name} weakly backed", _book_cause(book))
    return QueueReason("weak", "identity weakly backed", _book_cause(book))


def queue_reasons(book: BookUnit, *, root: Path, kind_of: KindOf) -> list[QueueReason]:
    """Every reason `book` needs a person, most urgent first. Empty when it needs nobody."""
    reasons: list[QueueReason] = []
    if has_blocking_error(book):
        if book.missing:
            reasons.append(QueueReason("blocked", "files missing from disk",
                                       _folder_cause(book, root, kind_of, _ENTITY_KINDS)
                                       or _missing_fallback_cause(book, root)))
        else:
            reasons.append(QueueReason("blocked", "a file can't be read", _book_cause(book)))
    # A confirmation settles everything but a blocking error; a finished or skipped book is done.
    if book.manually_confirmed or book.state in _SETTLED_STATES:
        return reasons
    if book.state is BookState.NEEDS_REVIEW:
        cause = _folder_cause(book, root, kind_of, _ENTITY_KINDS) or _book_cause(book)
        reasons.append(QueueReason("unsure", "identity unsure", cause))
    for finding in active_findings(book):
        if finding.code not in BLOCKING_FINDINGS:
            reasons.append(_finding_reason(book, finding, root, kind_of))
    # A weak score is only its own reason when nothing above already explains it.
    explained = any(r.kind in ("unsure", "finding") for r in reasons)
    if not explained and confidence_bucket(book) != "high":
        reasons.append(_weak_reason(book, root, kind_of))
    return reasons


def build_queue(books: Iterable[BookUnit], *, root_for: Callable[[Path], Path],
                kind_of: KindOf) -> Queue:
    """Group every queued book by (reason, problem, cause). Blocked groups first, then the biggest
    causes: one folder fix that clears twelve books outranks twelve one-off fixes."""
    groups: dict[tuple[str, str, Cause], QueueGroup] = {}
    queued: set[str] = set()
    for book in books:
        reasons = queue_reasons(book, root=root_for(book.source_folder), kind_of=kind_of)
        for reason in dict.fromkeys(reasons):
            key = (reason.kind, reason.phrase, reason.cause)
            group = groups.setdefault(key, QueueGroup(reason.kind, reason.phrase, reason.cause))
            group.books.append(book)
            queued.add(book.id)
    ordered = sorted(groups.values(),
                     key=lambda g: (g.kind != "blocked", -len(g.books), g.label))
    return Queue(groups=ordered, book_count=len(queued))


def check_matches(books: Iterable[BookUnit], threshold: float) -> list[BookUnit]:
    """Matched books whose provider fit stayed under the Ready threshold, worst fit first. Their own
    lane, not the queue: a weak fit says the match is uncertain, not that the book is wrong."""
    weak = [b for b in books
            if 0 < b.confidence < threshold and not b.manually_confirmed
            and not has_blocking_error(b)]
    return sorted(weak, key=lambda b: b.confidence)
