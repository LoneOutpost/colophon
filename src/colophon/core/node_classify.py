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
    books_by_folder: dict[Path, list[BookUnit]]   # SUBTREE books per folder (for tag/consensus/match)
    modal_author_depth: int | None                # from the TITLE-depth mode (author = mode - 1)
    book_like_children: dict[str, int]            # node id -> count of content (container/grouping) child dirs
    direct_books: dict[Path, list[BookUnit]] = field(default_factory=dict)   # a folder's own loose books
    overrides: dict[str, object] = field(default_factory=dict)               # path str -> NodeOverride


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
    """A folder holding its OWN loose books that span multiple series, or have no series at all,
    reads as an author (uses direct books, not the subtree — a folder-of-folders is a bucket, not a
    multi-series author). A node at the modal author depth gets a small tree-consistency nudge."""
    books = ctx.direct_books.get(node.path, [])
    out: list[Evidence] = []
    if books:
        by_series = _distinct_series(books)
        if len(by_series) >= 2:
            out.append(Evidence("author", 1.0 + 0.5 * len(by_series),
                                f"spans {len(by_series)} series across {len(books)} loose titles"))
        elif not by_series:
            out.append(Evidence("author", 1.5, f"{len(books)} loose books, no series information"))
    if ctx.modal_author_depth is not None and _depth(node.path, ctx.root) == ctx.modal_author_depth:
        out.append(Evidence("author", 0.5, "sits at the library's typical author depth"))
    return out


def ax_author_from_grouping(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform signature
    """A GROUPING (classify_graph found its children are mostly title folders) is an author/series
    folder — vote author; a genuine single-series grouping is pulled to series by ax_series_ramp."""
    from colophon.core.graph_classify import GROUPING
    if node.kind == GROUPING:
        return [Evidence("author", 2.0, "a folder of title subfolders (author/series grouping)")]
    return []


_SOFT_AUTHOR_PROV = frozenset({"tag", "datafile"})


def _tag_authors(books: list[BookUnit]) -> list[str]:
    """Authors on books whose author provenance is a soft, independent tier (tag/datafile)."""
    out: list[str] = []
    for b in books:
        if b.authors and b.provenance.get("authors") in _SOFT_AUTHOR_PROV:
            out.extend(b.authors)
    return out


def ax_tag_author_match(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A descendant book's tag/datafile author equals the folder name -> a soft author vote."""
    from colophon.core.graph_resolve import _name_key
    key = _name_key(node.path.name)
    for author in _tag_authors(ctx.books_by_folder.get(node.path, [])):
        if _name_key(author) == key:
            return [Evidence("author", 1.5, f"a tagged author matches the folder name '{author}'",
                             value=author)]
    return []


def ax_artist_consensus(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """When the folder's books agree on one tag/datafile author, that value IS the author — even if
    it differs from the folder name. Requires >= 2 agreeing books (a lone book is not a consensus)."""
    from collections import Counter

    from colophon.core.graph_resolve import _name_key
    authors = _tag_authors(ctx.books_by_folder.get(node.path, []))
    if len(authors) < 2:
        return []
    counts = Counter(_name_key(a) for a in authors)
    (top_key, top_n), = counts.most_common(1)
    if top_n >= 2 and (len(counts) == 1 or top_n >= 0.75 * len(authors)):
        display = next(a for a in authors if _name_key(a) == top_key)
        return [Evidence("author", min(3.0, 1.0 + 0.5 * top_n),
                         f"{top_n} books agree on tagged author '{display}'", value=display)]
    return []


_MATCH_SOURCES = frozenset({"audnexus", "audible", "hardcover", "openlibrary"})
_SERIES_COVERAGE = 0.6


def ax_series_ramp(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """All/most books one series with a sequence ramp AND the folder name resembles it -> series."""
    from colophon.core.graph_classify import _series_label
    from colophon.core.graph_resolve import _resembles
    books = ctx.books_by_folder.get(node.path, [])
    if not books:
        return []
    by_series = _distinct_series(books)
    if len(by_series) != 1:
        return []
    (_key, seqs), = by_series.items()
    if len(seqs) / len(books) < _SERIES_COVERAGE:
        return []
    ramp = sorted({s for s in seqs if s is not None})
    display = next(_series_label(b)[1] for b in books if _series_label(b))
    if len(ramp) >= 2 and _resembles(node.path.name, display):
        return [Evidence("series", 3.0,
                         f"all books in series '{display}' (seq {ramp[0]:g}-{ramp[-1]:g}), folder matches",
                         value=display)]
    return []


def ax_matched_identity(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """Books positively identified by a match source that agree on an author equal to the folder
    name settle the node as that author (hard)."""
    from colophon.core.graph_resolve import _name_key
    key = _name_key(node.path.name)
    matched_authors = [
        a for b in ctx.books_by_folder.get(node.path, [])
        if b.provenance.get("authors") in _MATCH_SOURCES for a in b.authors
    ]
    for author in matched_authors:
        if _name_key(author) == key:
            return [Evidence("author", 10.0, f"matched book(s) author '{author}' == folder name",
                             hard=True, value=author)]
    return []


def ax_manual_override(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A persisted user classification settles the node (hard), whatever kind they chose."""
    ov = ctx.overrides.get(str(node.path))
    if ov is None:
        return []
    return [Evidence(ov.kind, 100.0, "you classified this folder", hard=True, value=ov.value)]
