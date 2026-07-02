"""Weighted-evidence node classifier: pure axioms emit votes, the resolver tallies them into a
Classification (kind + value + confidence + source + evidence). Soft votes accumulate a mutable
confidence store; hard evidence (a match or a manual confirmation) settles the node. Replaces the
imperative resolve_graph_authors/hint_grouping_kinds passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit

# Fixed candidate order — used to break exact soft ties deterministically.
_KIND_ORDER = ("author", "series", "franchise", "container")

_BUCKET_WORDS = frozenset({
    "incoming", "downloads", "download", "audiobooks", "audiobook", "books", "misc",
    "unsorted", "new", "temp", "tmp", "media", "library", "import", "imports",
})


@dataclass(frozen=True)
class Evidence:
    kind: str                  # "author" | "series" | "franchise" | "container"
    weight: float              # confidence contribution toward `kind` (> 0)
    reason: str                # human-readable — feeds kind_evidence + the provenance readout
    hard: bool = False         # True = certainty (a match or a manual confirmation)
    value: str | None = None   # a suggested kind value (author/series name), when known


@dataclass(frozen=True)
class Classification:
    kind: str
    value: str | None
    confidence: float
    source: str                        # "" (auto/soft) | "manual" | "matched"
    settled: bool
    evidence: list[Evidence] = field(default_factory=list)


def _value_for(kind: str, evidence: list[Evidence], fallback_value: str | None) -> str | None:
    """The winning kind's name: the highest-weight evidence of that kind that carries a value, else
    the folder-name fallback for author/series (container/franchise carry no fallback name)."""
    with_value = [e for e in evidence if e.kind == kind and e.value]
    if with_value:
        return max(with_value, key=lambda e: e.weight).value
    return fallback_value if kind in ("author", "series") else None


def resolve(
    evidence: list[Evidence], *, fallback_value: str | None = None,
    manual_kinds: frozenset[str] | set[str] = frozenset(),
    matched_kinds: frozenset[str] | set[str] = frozenset(),
) -> Classification:
    """Tally `evidence` into a Classification. Hard evidence settles the node (manual > matched);
    otherwise the highest summed-weight kind wins with a margin confidence. `manual_kinds`/
    `matched_kinds` tell the resolver which hard votes came from a user override vs a match, so it
    can apply manual-over-matched precedence and stamp `source`."""
    hard = [e for e in evidence if e.hard]
    if hard:
        manual = [e for e in hard if e.kind in manual_kinds]
        pool = manual or hard
        winner = max(pool, key=lambda e: e.weight)
        source = "manual" if winner.kind in manual_kinds else ("matched" if winner.kind in matched_kinds else "manual")
        return Classification(
            kind=winner.kind, value=winner.value or _value_for(winner.kind, evidence, fallback_value),
            confidence=1.0, source=source, settled=True, evidence=list(evidence),
        )
    if not evidence:
        return Classification("container", None, 0.0, "", False, [])
    totals: dict[str, float] = {}
    for e in evidence:
        totals[e.kind] = totals.get(e.kind, 0.0) + e.weight
    total = sum(totals.values())
    best = max(_KIND_ORDER, key=lambda k: (totals.get(k, 0.0), -_KIND_ORDER.index(k)))
    confidence = round(totals.get(best, 0.0) / total, 2) if total else 0.0
    return Classification(
        kind=best, value=_value_for(best, evidence, fallback_value),
        confidence=confidence, source="", settled=False, evidence=list(evidence),
    )


@dataclass
class _Ctx:
    graph: Graph
    root: Path
    books_by_folder: dict[Path, list[BookUnit]]   # subtree books per folder path
    modal_author_depth: int | None                # from the TITLE-depth mode (author = mode - 1)
    book_like_children: dict[str, int]            # node id -> count of child dirs that hold books
    overrides: dict[str, object] = field(default_factory=dict)   # path str -> NodeOverride


def _depth(path: Path, root: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return 0


def ax_container_shape(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """Structural container evidence: a folder-of-folders is a bucket (weight grows with the count),
    loose audio beside subfolders is mixed, and the scan root is usually a library bucket."""
    out: list[Evidence] = []
    m = ctx.book_like_children.get(node.id, 0)
    if m >= 2:
        out.append(Evidence("container", 1.0 + 0.5 * m, f"{m} book-like child folders (a bucket)"))
    if node.child_files and node.child_dirs:
        out.append(Evidence("container", 1.0, f"loose audio beside {len(node.child_dirs)} subfolders"))
    if node.path == ctx.root:
        out.append(Evidence("container", 1.0, "the scan root is usually a library bucket"))
    return out


def ax_bucket_word(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform axiom signature
    """A bucket/stop word or a numeric name is not an author. Capitalization and single-token names
    are intentionally ignored (noisy here; single-name/alias authors are legitimate)."""
    name = node.path.name
    low = name.strip().casefold()
    if low in _BUCKET_WORDS:
        return [Evidence("container", 2.0, f"'{name}' is a bucket/staging folder name")]
    if low.replace(" ", "").isdigit():
        return [Evidence("container", 1.5, f"'{name}' is numeric, not a person/author name")]
    return []


def _distinct_series(books: list[BookUnit]) -> dict[str, list[float | None]]:
    """Map normalized-series-key -> sequences, across `books` that carry a series."""
    from colophon.core.graph_classify import _series_label
    by: dict[str, list[float | None]] = {}
    for b in books:
        label = _series_label(b)
        if label is not None:
            by.setdefault(label[0], []).append(label[2])
    return by


def ax_author_structure(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder spanning multiple series/titles, or holding loose books with no series, reads as an
    author; a node at the modal author depth gets a small tree-consistency nudge."""
    books = ctx.books_by_folder.get(node.path, [])
    if not books:
        return []
    out: list[Evidence] = []
    by_series = _distinct_series(books)
    if len(by_series) >= 2:
        out.append(Evidence("author", 1.0 + 0.5 * len(by_series),
                            f"spans {len(by_series)} series across {len(books)} titles"))
    elif not by_series:
        out.append(Evidence("author", 1.5, f"{len(books)} loose books, no series information"))
    if ctx.modal_author_depth is not None and _depth(node.path, ctx.root) == ctx.modal_author_depth:
        out.append(Evidence("author", 0.5, "sits at the library's typical author depth"))
    return out
