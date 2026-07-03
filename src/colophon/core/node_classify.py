"""Weighted-evidence node classifier: pure axioms emit votes, the resolver tallies them into a
Classification (kind + value + confidence + source + evidence). Soft votes accumulate a mutable
confidence store; hard evidence (a match or a manual confirmation) settles the node. Replaces the
imperative resolve_graph_authors/hint_grouping_kinds passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit

# Fixed candidate order — used to break exact soft ties deterministically. `title` is the most
# specific (a book-identity leaf) so it wins ties.
_KIND_ORDER = ("title", "author", "series", "franchise", "container")

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
    value_evidenced: bool = False      # value came from book evidence, not the folder-name fallback


def _valued(kind: str, evidence: list[Evidence]) -> bool:
    """Whether any evidence of `kind` carries a concrete value (a book-derived name) — i.e. the
    resolved value will come from evidence rather than the folder-name fallback."""
    return any(e.kind == kind and e.value for e in evidence)


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
        # manual > matched; a hard vote that is neither (a future hard axiom the caller did not
        # register in matched_kinds) stamps neutral "" — never a forged user "manual" confirmation.
        if winner.kind in manual_kinds:
            source = "manual"
        elif winner.kind in matched_kinds:
            source = "matched"
        else:
            source = ""
        return Classification(
            kind=winner.kind, value=winner.value or _value_for(winner.kind, evidence, fallback_value),
            confidence=1.0, source=source, settled=True, evidence=list(evidence),
            value_evidenced=bool(winner.value or _valued(winner.kind, evidence)),
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
        value_evidenced=_valued(best, evidence),
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
    known_franchises: dict[str, str] = field(default_factory=dict)   # name_key -> display
    author_depth: int | None = None   # scheme depth (1-based) whose folder is the author, or None


def _depth(path: Path, root: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return 0


def _author_depth(scheme: str) -> int | None:
    """The 1-based directory depth at which the configured scheme places the author, or 1 for a
    blank scheme (the near-universal Root/Author/... convention). None when the scheme is set but
    has no $Author level, so we make no directory-author assumption."""
    from colophon.core.dirinfer import parse_scheme
    patterns = parse_scheme(scheme)
    if not patterns:
        return 1
    for i, pat in enumerate(patterns, start=1):
        if "author" in pat.groupindex:
            return i
    return None


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
        # a strong (but soft) prior: the scan path is usually a library bucket, not one author's
        # folder. Enough to outweigh a lone structural-author vote so a bare root does not get
        # named after the upload folder — but still yields to real author evidence (a tag
        # consensus or a match), so a genuine single-author root can emerge.
        out.append(Evidence("container", 2.5, "the scan root is usually a library bucket"))
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


def _is_known_franchise(node: DirectoryNode, ctx: _Ctx) -> bool:
    """True when the folder's name matches a declared/seeded franchise. A franchise and an author
    are mutually exclusive for one folder, so a franchise match suppresses the structural author
    guess (which otherwise reads a franchise's many series as strong authorship)."""
    from colophon.core.graph_resolve import _name_key
    return _name_key(node.path.name) in ctx.known_franchises


def ax_author_structure(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder holding >= 2 of its OWN loose books reads as an author — UNLESS those books are all
    one series whose name matches the folder (a genuine series folder, which ax_series_ramp votes),
    or the folder is a known franchise (never an author). Uses direct books (a folder-of-folders is
    a bucket, not a multi-series author); a single book is a title, not an author. A node at the
    modal author depth gets a small tree-consistency nudge."""
    if _is_known_franchise(node, ctx):
        return []
    from colophon.core.graph_classify import _series_label
    from colophon.core.graph_resolve import _resembles
    books = ctx.direct_books.get(node.path, [])
    out: list[Evidence] = []
    if len(books) >= 2:
        by_series = _distinct_series(books)
        single_matching = False
        if len(by_series) == 1:
            display = next(_series_label(b)[1] for b in books if _series_label(b))
            single_matching = _resembles(node.path.name, display)
        if not single_matching:
            reason = (f"spans {len(by_series)} series across {len(books)} loose titles" if by_series
                      else f"{len(books)} loose books, no series information")
            out.append(Evidence("author", 1.0 + 0.5 * max(len(by_series), 1), reason))
    if ctx.modal_author_depth is not None and _depth(node.path, ctx.root) == ctx.modal_author_depth:
        out.append(Evidence("author", 0.5, "sits at the library's typical author depth"))
    return out


def ax_leaf_title(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform signature
    """A single-book leaf (classify_graph's TITLE) is that book's title folder — a strong structural
    vote that keeps it from being pulled up to author/series by a lone tag on its one book."""
    from colophon.core.graph_classify import TITLE
    if node.kind == TITLE:
        return [Evidence("title", 5.0, "single-book leaf (a title folder)")]
    return []


def ax_author_from_grouping(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform signature
    """A GROUPING (classify_graph found its children are mostly title folders) is an author/series
    folder — vote author; a genuine single-series grouping is pulled to series by ax_series_ramp,
    and a known franchise (never an author) is suppressed."""
    from colophon.core.graph_classify import GROUPING
    if node.kind == GROUPING and not _is_known_franchise(node, ctx):
        return [Evidence("author", 2.0, "a folder of title subfolders (author/series grouping)")]
    return []


def ax_known_franchise(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder whose name exactly matches a user-declared franchise votes franchise — soft,
    competing evidence (weight 4.0) that beats a structural author guess but yields to a match
    (hard 10.0) or a manual override (hard 100.0), and to a genuine single-book title (5.0)."""
    from colophon.core.graph_resolve import _name_key
    display = ctx.known_franchises.get(_name_key(node.path.name))
    if display:
        return [Evidence("franchise", 4.0, f"declared franchise '{display}'", value=display)]
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
    """When the folder's tagged books agree on one author, that value IS the author, even if it
    differs from the folder name (a lone tag is weak but still names the author; container weight
    outvotes a stray tag at a bucket root). A >=75% supermajority counts as agreement, so one
    mis-tagged or guest-author book does not block the vote; no vote below that."""
    from collections import Counter

    from colophon.core.graph_resolve import _name_key
    authors = _tag_authors(ctx.books_by_folder.get(node.path, []))
    if not authors:
        return []
    counts = Counter(_name_key(a) for a in authors)
    (top_key, top_n), = counts.most_common(1)
    if top_n == len(authors) or top_n >= 0.75 * len(authors):   # agreement, no rival tag author
        display = next(a for a in authors if _name_key(a) == top_key)
        return [Evidence("author", min(3.0, 0.5 + 0.5 * top_n),
                         f"{top_n} book(s) tagged author '{display}'", value=display)]
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


def _child_name(node_path: Path, book: BookUnit) -> str:
    """The name of the node directly under `node_path` on the way to `book`: the book's
    sub-folder name, or its filename stem when the book sits directly in the folder (flat layout)."""
    folder = book.source_folder
    if folder == node_path:
        return Path(book.source_files[0].path).stem if book.source_files else folder.name
    try:
        return folder.relative_to(node_path).parts[0]
    except ValueError:
        return folder.name


def _series_tag_present(books: list[BookUnit]) -> bool:
    """True when a book independently asserts a series (tag/datafile) — corroboration that its
    numbered siblings really are a series."""
    return any(b.series and b.provenance.get("series") in _SOFT_AUTHOR_PROV for b in books)


def ax_numbered_siblings(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder whose child books carry sequence-number affixes ('02 - Yendi', '03 - Teckla', …)
    is a series ramp — structural series evidence that exists BEFORE any series field does (unlike
    ax_series_ramp, which needs the field). Additive: an attention trigger, a distinct-title ramp,
    and optional tag corroboration; the resolve() sum decides against the author-grouping vote."""
    from colophon.core.sequence_affix import parse_sequence_affix
    books = ctx.books_by_folder.get(node.path, [])
    if not books:
        return []
    parsed: dict[str, object] = {}          # one entry per direct child name (SequenceAffix)
    for b in books:
        name = _child_name(node.path, b)
        aff = parse_sequence_affix(name)
        if aff is not None:
            parsed.setdefault(name, aff)
    if not parsed:
        return []
    value = node.path.name
    evidence = [Evidence("series", 1.0, f"{len(parsed)} child name(s) carry a sequence number",
                         value=value)]
    nums = {a.sequence for a in parsed.values()}
    titles = {a.cleaned.casefold() for a in parsed.values()}
    has_strong = any(a.confidence == "strong" for a in parsed.values())
    corroborated = _series_tag_present(books)
    if len(parsed) >= 2 and len(nums) >= 2 and len(titles) >= 2 and (has_strong or corroborated):
        lo, hi = min(nums), max(nums)
        evidence.append(Evidence("series", 2.0,
                                 f"numbered title ramp (seq {lo:g}-{hi:g}, distinct titles)",
                                 value=value))
    if corroborated:
        evidence.append(Evidence("series", 1.0, "child books carry a series tag", value=value))
    return evidence


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


_WEAK = frozenset({"directory", "filename"})
_AXIOMS = (
    ax_manual_override, ax_matched_identity,          # hard
    ax_artist_consensus, ax_tag_author_match,         # author (name-bearing)
    ax_leaf_title,                                     # title (book-identity leaf)
    ax_author_structure, ax_author_from_grouping, ax_known_franchise,
    ax_numbered_siblings, ax_series_ramp,                                                # author/series/franchise (structural)
    ax_container_shape, ax_bucket_word,               # container
)


def _build_ctx(graph: Graph, root: Path, overrides: dict[str, object],
               known_franchises: dict[str, str], directory_scheme: str = "") -> _Ctx:
    from collections import Counter

    from colophon.core.graph_classify import CONTAINER, GROUPING, TITLE, _subtree_books
    books_by_folder = {d.path: _subtree_books(graph, d) for d in graph.directories.values()}
    direct_books = {
        d.path: [graph.books[b].book for b in d.books if b in graph.books]
        for d in graph.directories.values()
    }
    title_depths = Counter(_depth(d.path, root) for d in graph.directories.values() if d.kind == TITLE)
    modal = (title_depths.most_common(1)[0][0] - 1) if title_depths else None
    # A "book-like child" for the bucket signal is a child dir classify_graph coarse-typed as content
    # (container/grouping) — NOT a title child (a folder of titles is an author grouping, not a bucket).
    book_like = {
        d.id: sum(1 for c in d.child_dirs
                  if c in graph.directories and graph.directories[c].kind in (CONTAINER, GROUPING))
        for d in graph.directories.values()
    }
    return _Ctx(graph=graph, root=root, books_by_folder=books_by_folder, modal_author_depth=modal,
                book_like_children=book_like, direct_books=direct_books, overrides=overrides,
                known_franchises=known_franchises, author_depth=_author_depth(directory_scheme))


def classify_nodes(
    graph: Graph, books: list[BookUnit], *, root: Path, overrides: dict[str, object],
    known_franchises: dict[str, str] | None = None, directory_scheme: str = "",
) -> None:
    """Classify every directory node from accumulated axiom evidence, write the result onto the node,
    then fill empty/weak-author books from the nearest author node (GRAPHING)."""
    ctx = _build_ctx(graph, root, overrides, known_franchises or {}, directory_scheme)
    evidenced: dict[str, bool] = {}
    for node in graph.directories.values():
        evidence: list[Evidence] = []
        for axiom in _AXIOMS:
            evidence.extend(axiom(node, ctx))
        ov = ctx.overrides.get(str(node.path))
        manual_kinds = {ov.kind} if ov is not None else set()
        matched_kinds = {e.kind for e in evidence if e.hard} - manual_kinds
        c = resolve(evidence, fallback_value=node.path.name,
                    manual_kinds=manual_kinds, matched_kinds=matched_kinds)
        node.kind = c.kind
        node.author = c.value if c.kind == "author" else None
        node.kind_value = c.value
        node.kind_confidence = c.confidence
        node.kind_source = c.source
        node.kind_evidence = [e.reason for e in c.evidence]
        evidenced[node.id] = c.value_evidenced
    _fill_down(graph, books, evidenced, root=root, author_depth=ctx.author_depth)
    _fill_series_ramp(graph, books, root=root)


def _nearest_series(graph: Graph, folder: Path, root: Path) -> DirectoryNode | None:
    """The nearest ancestor (incl. `folder`) classified `series`, or None — walking to root."""
    cur = folder
    while True:
        node = graph.directories.get(DirectoryNode.id_for(cur))
        if node is not None and node.kind == "series":
            return node
        if cur == root or root not in cur.parents:
            return None
        cur = cur.parent


def _fill_series_ramp(graph: Graph, books: list[BookUnit], *, root: Path) -> None:
    """For a book under a folder classified `series`, take its sequence from the child-name affix
    (the reliable position number) and stamp series name + sequence when it has no stronger series;
    separately clean the affix off the book's OWN title (so a good title isn't overwritten by a
    misspelled folder). GRAPHING provenance; MATCH overrules. Never touch a tag/datafile/match/manual
    series or title."""
    from colophon.core.models import Provenance, SeriesRef
    from colophon.core.sequence_affix import parse_sequence_affix
    fillable = _WEAK | {Provenance.GRAPHING.value}
    for book in books:
        node = _nearest_series(graph, book.source_folder, root)
        if node is None or not node.kind_value:
            continue
        aff = parse_sequence_affix(_child_name(node.path, book))
        if aff is None:
            continue
        if not book.series or book.provenance.get("series") in fillable:
            book.series = [SeriesRef(name=node.kind_value, sequence=aff.sequence)]
            book.provenance["series"] = Provenance.GRAPHING.value
        taff = parse_sequence_affix(book.title or "")
        if (taff is not None and taff.cleaned != book.title
                and book.provenance.get("title") in _WEAK
                and (taff.confidence == "strong" or aff.confidence == "strong")):
            # a strong title affix, or a strong ramp position (aff) corroborating a weak title one —
            # so a manual/match series node with a weak compound title (e.g. '30-Day…') is left alone
            book.title = taff.cleaned


def _fill_down(graph: Graph, books: list[BookUnit], evidenced: dict[str, bool], *,
               root: Path, author_depth: int | None) -> None:
    """Inherit an author into each empty/weak-author book, walking leaf->root. Prefer the nearest
    classified author node (evidence-named over a folder-name fallback, so an intermediate grouping
    can't shadow the real author); failing that, fall back to the folder at the directory scheme's
    author depth (the declared layout) — but never a folder classified franchise/series/container,
    whose name is not an author. Never overwrite a book's own hard (tag/datafile/match/manual)
    author."""
    from colophon.core.models import Provenance
    from colophon.core.normalize import proper_case_if_shouting
    non_author = {"franchise", "series", "container"}
    for book in books:
        prov = book.provenance.get("authors")
        if book.authors and prov not in _WEAK:
            continue
        seen: list[DirectoryNode] = []          # classified-author ancestors, nearest first
        layout: DirectoryNode | None = None     # the ancestor at the scheme's author depth
        cur = book.source_folder
        while True:
            node = graph.directories.get(DirectoryNode.id_for(cur))
            if node is not None:
                if node.kind == "author" and node.author:
                    seen.append(node)
                if (author_depth is not None and _depth(cur, root) == author_depth
                        and node.kind not in non_author):
                    layout = node
            if cur == root or root not in cur.parents:
                break
            cur = cur.parent
        chosen = next((n for n in seen if evidenced.get(n.id)), seen[0] if seen else None)
        if chosen is not None:
            # a user-confirmed (manual) author node propagates as MANUAL; an inferred one as GRAPHING
            name = chosen.author
            provenance = (Provenance.MANUAL.value if chosen.kind_source == "manual"
                          else Provenance.GRAPHING.value)
        elif layout is not None:
            name = layout.path.name
            provenance = Provenance.DIRECTORY.value
        else:
            continue
        # proper-case a shouting inherited/layout name ('STEPHEN COONTS' -> 'Stephen Coonts'); a
        # user's manual value is kept verbatim (authoritative spelling).
        if provenance != Provenance.MANUAL.value:
            name = proper_case_if_shouting(name)
        # only (re)stamp when we actually introduce the value, so a book's own more-specific weak
        # provenance (directory/filename) survives when it already agrees.
        if book.authors != [name]:
            book.authors = [name]
            book.provenance["authors"] = provenance
