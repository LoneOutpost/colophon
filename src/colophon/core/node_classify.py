"""Weighted-evidence node classifier: pure axioms emit votes, the resolver tallies them into a
Classification (kind + value + confidence + source + evidence). Soft votes accumulate a mutable
confidence store; hard evidence (a match or a manual confirmation) settles the node. Replaces the
imperative resolve_graph_authors/hint_grouping_kinds passes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from colophon.core.ballot import tally
from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import WEAK_PROV, BookUnit, NodeOverride
from colophon.core.normalize import collides_with_title

if TYPE_CHECKING:
    from colophon.core.sequence_affix import SequenceAffix

# Fixed candidate order — used to break exact soft ties deterministically. `title` is the most
# specific (a book-identity leaf) so it wins ties.
_KIND_ORDER = ("title", "author", "series", "franchise", "container")
_AUTHOR_JUNK_REJECT = 0.9   # a node author value at/above this junk magnitude is not a usable author name

# --- Evidence weight ladder ------------------------------------------------------------------
# Soft votes SUM; the highest-weighted kind wins. Hard votes (manual/match) settle outright.
# All weights now live in colophon.core.evidence_weights, the single tuning surface; imported here
# so the axioms below read the same names as before.
from colophon.core.evidence_weights import (  # noqa: E402
    W_AUTHOR_GROUPING,
    W_AUTHOR_STRUCTURE_MAX,
    W_BUCKET_BASE,
    W_BUCKET_PER_CHILD,
    W_BUCKET_WORD,
    W_CONSENSUS_MAX,
    W_FRANCHISE,
    W_LEAF_AUTHOR,
    W_LEAF_SERIES,
    W_MANUAL,
    W_MATCH,
    W_MEMOIR_AUTHOR,
    W_MIXED_LOOSE,
    W_MODAL_DEPTH_NUDGE,
    W_NUMBERED_BASE,
    W_NUMBERED_RAMP,
    W_NUMBERED_TAG,
    W_NUMERIC_NAME,
    W_ROOT_PRIOR,
    W_SERIES_RAMP,
    W_TAG_AUTHOR_MATCH,
    W_TITLE_LEAF,
)

_BUCKET_WORDS = frozenset({
    "incoming", "downloads", "download", "audiobooks", "audiobook", "books", "misc",
    "unsorted", "new", "temp", "tmp", "media", "library", "import", "imports",
})

# Content-collection folder names — an author's non-series / standalone / anthology shelf. NOT an
# author (unlike a person-named grouping). Distinct from _BUCKET_WORDS (staging/library roots). Stored
# in normalized form (casefold, non-alphanumerics -> single space); membership is EXACT, not substring,
# so an author "Anthony" or a title "Best of Times" is never caught. A compound name ("Best of Queen")
# is intentionally not matched — precision over recall, since suppressing a real author is worse.
_COLLECTION_WORDS = frozenset({
    "non series", "standalone", "collection", "collections", "anthology", "anthologies",
    "omnibus", "miscellaneous", "misc", "short stories", "singles", "best of",
})
_COLLECTION_NORM = re.compile(r"[^a-z0-9]+")


def _is_collection_name(node: DirectoryNode) -> bool:
    """True when the folder name is a content-collection bucket (Non-Series / Standalone / Anthology),
    so it is not itself an author. Author axioms abstain for these."""
    norm = _COLLECTION_NORM.sub(" ", node.path.name.casefold()).strip()
    return norm in _COLLECTION_WORDS


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
    the folder-name fallback for author/series (container/franchise carry no fallback name). A junk-
    shaped AUTHOR value (a whole 'Author.-.Title' folder string, a structural marker) is rejected —
    it is not an author name, so the node contributes none and the real author resolves from a cleaner
    ancestor (the parent author folder) instead of filling the junk down."""
    from colophon.core.metadata_quality import author_junk
    with_value = [e for e in evidence if e.kind == kind and e.value]
    value = (max(with_value, key=lambda e: e.weight).value if with_value
             else (fallback_value if kind in ("author", "series") else None))
    if kind == "author" and value and author_junk(value) >= _AUTHOR_JUNK_REJECT:
        return None
    return value


def resolve(
    evidence: list[Evidence], *, fallback_value: str | None = None,
    manual_kinds: frozenset[str] | set[str] = frozenset(),
    matched_kinds: frozenset[str] | set[str] = frozenset(),
) -> Classification:
    """Tally `evidence` into a Classification. Hard evidence settles the node (manual > matched);
    otherwise the highest summed-weight kind wins, with confidence = that kind's SHARE of the total
    evidence weight (not a margin over the runner-up, so a lone unopposed vote reads 1.0 however
    weak). `manual_kinds`/`matched_kinds` tell the resolver which hard votes came from a user
    override vs a match, so it can apply manual-over-matched precedence and stamp `source`."""
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
    t = tally([(e.kind, e.weight) for e in evidence], order=_KIND_ORDER)
    best = t.winner
    return Classification(
        kind=best, value=_value_for(best, evidence, fallback_value),
        confidence=t.share, source="", settled=False, evidence=list(evidence),
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
    overrides: dict[str, NodeOverride] = field(default_factory=dict)         # path str -> NodeOverride
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
        out.append(Evidence("container", W_BUCKET_BASE + W_BUCKET_PER_CHILD * m,
                            f"{m} book-like child folders (a bucket)"))
    if node.child_files and node.child_dirs:
        out.append(Evidence("container", W_MIXED_LOOSE,
                            f"loose audio beside {len(node.child_dirs)} subfolders"))
    if node.path == ctx.root:
        # a strong (but soft) prior: the scan path is usually a library bucket, not one author's
        # folder. Enough to outweigh a lone structural-author vote so a bare root does not get
        # named after the upload folder — but still yields to real author evidence (a tag
        # consensus or a match), so a genuine single-author root can emerge.
        out.append(Evidence("container", W_ROOT_PRIOR, "the scan root is usually a library bucket"))
    return out


def ax_bucket_word(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform axiom signature
    """A bucket/stop word or a numeric name is not an author. Capitalization and single-token names
    are intentionally ignored (noisy here; single-name/alias authors are legitimate)."""
    name = node.path.name
    low = name.strip().casefold()
    if low in _BUCKET_WORDS:
        return [Evidence("container", W_BUCKET_WORD, f"'{name}' is a bucket/staging folder name")]
    if low.replace(" ", "").isdigit():
        return [Evidence("container", W_NUMERIC_NAME, f"'{name}' is numeric, not a person/author name")]
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
    if _is_known_franchise(node, ctx) or _is_collection_name(node):
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
            # Capped: 2 distinct series already proves a multi-book author; more series don't make it
            # MORE an author, and an uncapped vote would swamp the franchise/root-prior tiers.
            weight = min(W_AUTHOR_STRUCTURE_MAX, 1.0 + 0.5 * max(len(by_series), 1))
            out.append(Evidence("author", weight, reason))
    if ctx.modal_author_depth is not None and _depth(node.path, ctx.root) == ctx.modal_author_depth:
        out.append(Evidence("author", W_MODAL_DEPTH_NUDGE, "sits at the library's typical author depth"))
    return out


# Title provenances that only echo the folder name back (directory inference / graph fill), so they
# are NOT evidence the folder is a title. A title read from the file itself (tag/datafile/filename/
# manual/match) is real evidence.
_CIRCULAR_TITLE_PROV = frozenset({"directory", "graphing"})

# Phrases that mark a title as a memoir/autobiography. High-precision on purpose: a memoir is often
# titled after its subject, so an author-named folder whose book title contains one of these AND
# embeds the author's name is the author's folder, not a title folder.
_MEMOIR_MARKERS = ("memoir", "autobiography", "my story", "the story of", "my life")


def _is_memoir_titled(title: str) -> bool:
    low = title.casefold()
    return any(m in low for m in _MEMOIR_MARKERS)


def _name_is_proper_subset(name: str, title: str) -> bool:
    """True when every token of `name` appears in `title` AND `title` has more — i.e. the folder
    (author) name is embedded in a strictly longer title, not equal to it."""
    from colophon.core.graph_resolve import _series_tokens
    a, b = _series_tokens(name), _series_tokens(title)
    return bool(a) and a < b


def ax_leaf_title(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform signature
    """Decide a single-book leaf's folder by elimination against the one book's own fields. If the
    folder name resembles the book's real (file-sourced) title, the folder IS the title folder
    (Root/.../Title layout). Else if it resembles the book's series, it is a series folder. Else,
    unless the name is a known franchise or a bucket/numeric label, the folder can only be the author
    — the very common Root/Author/OneBook.mp3 layout, where a lone book would otherwise be misread as
    its own title.

    Crucially, a title that is only the folder name echoed back by directory inference is NOT title
    evidence (that reasoning is circular): a folder whose book has no file-supplied title, series, or
    franchise falls through to author, and the role-driven weak stage (`attribute(role="author")`)
    then re-derives the book's title from the filename. On a MULTI-FILE book (more than one source
    file) the cluster label is an internal
    part/section, not a real title, so it is excluded from `file_title` and cannot trigger the author
    fallback; only a single-file book's label still counts (the very common Author/OneBook.mp3 layout).
    The author vote is deliberately weaker than a tagged-author consensus, so an embedded tag still
    wins the node's author VALUE when folder name and tag disagree."""
    from colophon.core.filename_cluster import _text_sig, _tokens
    from colophon.core.graph_classify import TITLE, _series_label
    from colophon.core.graph_resolve import _resembles
    if node.kind != TITLE:
        return []
    books = ctx.direct_books.get(node.path, [])
    book = books[0] if books else None
    name = node.path.name
    if book is None:
        return [Evidence("title", W_TITLE_LEAF, "single-book leaf (a title folder)")]
    # The title the FILE genuinely supplies: a hard-sourced (tag/datafile/match) title only. A
    # directory/graphing title is a folder-name echo; a filename title is the cluster label. On a
    # MULTI-FILE book the cluster label is an internal PART (a section/chapter), never a title, so it
    # cannot make a title folder look like an author. On a lone SINGLE-FILE book the filename IS the
    # title (Author/OneBook.mp3), so the label still counts and a real author folder stays author.
    real_title = (book.title if book.title
                  and book.provenance.get("title") in _HARD_IDENTITY_PROV else None)
    # The cluster label from filename scanning: comes from detected_works when present; when the
    # identifier stored the label directly on book.title with a non-echo provenance (e.g. "filename"
    # or None), use that — same data, different storage path.
    file_label = (book.detected_works[0].label if book.detected_works else
                  (book.title if book.title and not real_title
                   and book.provenance.get("title") not in _CIRCULAR_TITLE_PROV else None))
    label_has_text = bool(file_label) and bool(_text_sig(_tokens(file_label)))  # real words, not "01"
    # A MULTI-FILE book's cluster label is an internal PART (section/chapter), not a real title, and
    # must not flip a title folder to author. A single-file (or untracked) book's label IS its title.
    multi_file = len(book.source_files) > 1
    file_title = real_title or (file_label if label_has_text and not multi_file else None)
    has_real_author = bool(book.authors) and book.provenance.get("authors") in _HARD_IDENTITY_PROV
    at_author_depth = ctx.author_depth is not None and _depth(node.path, ctx.root) == ctx.author_depth
    if file_title and _resembles(name, file_title):
        # A memoir/autobiography is often titled after its subject, so an author-named folder whose
        # book title embeds the author's name ('Sam Walton' -> 'Sam Walton, made in America, my
        # story') reads like a title match but is really the author's folder. Only flip when the
        # folder is a strict fragment of a memoir-marked title, at the author depth, with no author of
        # its own — additive, never fires on a non-memoir and never demotes a real title.
        if (at_author_depth and not has_real_author
                and _is_memoir_titled(file_title) and _name_is_proper_subset(name, file_title)):
            return [Evidence("author", W_MEMOIR_AUTHOR,
                             "memoir/autobiography title contains the author's name", value=name)]
        return [Evidence("title", W_TITLE_LEAF, "single-book leaf; folder name matches the title")]
    label = _series_label(book)
    if label is not None and _resembles(name, label[1]):
        return [Evidence("series", W_LEAF_SERIES, f"single-book leaf; folder matches series '{label[1]}'",
                         value=label[1])]
    low = name.strip().casefold()
    if _is_known_franchise(node, ctx) or low in _BUCKET_WORDS or low.replace(" ", "").isdigit():
        return []  # a franchise / bucket / numeric leaf is not an author; its own axioms decide
    # The folder is the AUTHOR only for a lone book sitting directly in an author slot: the book has
    # no real author of its own (a tag/datafile/match author means the folder is the title, not the
    # author); the folder sits exactly at the library's author depth (a leaf nested BELOW the author
    # level is inside an author's own subtree, so it is a title); and the FILE supplies a real title
    # distinct from the folder (a bare track number like "01" identifies no title, so the folder name
    # stays the title). Otherwise it is a title folder; the role-driven weak stage
    # (`attribute(role="author")`) re-derives the book's real title from the filename.
    if not has_real_author and at_author_depth and file_title is not None:
        return [Evidence("author", W_LEAF_AUTHOR,
                         "lone book at the author depth; folder names the author", value=name)]
    return [Evidence("title", W_TITLE_LEAF, "single-book leaf (a title folder)")]


def ax_folder_title_shape(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A single-book leaf whose folder name is a strong title shape — a leading `YEAR -` prefix, a
    parsed `read by` narrator, or a `(Series Book #N)` / `#N -` book-in-series prefix with a real title
    remainder — is that book's title folder. Raw folder-name evidence only, so it holds even when the
    filenames inside are internal parts/sections that disagree with the folder. Weighted like a leaf
    title so it beats the lone-book->author fallback (W_LEAF_AUTHOR) and the series-resemblance vote."""
    from colophon.core.filename_cluster import _text_sig, _tokens
    from colophon.core.folder_title import parse_folder_title
    from colophon.core.graph_classify import TITLE
    if node.kind != TITLE:                       # coarse-type gate: single-book leaves only
        return []
    if len(ctx.direct_books.get(node.path, [])) != 1:
        return []
    parsed = parse_folder_title(node.path.name)
    if parsed.year is not None or parsed.narrators:
        return [Evidence("title", W_TITLE_LEAF,
                         "folder name is a year/narrator title shape")]
    if (parsed.series is not None or parsed.sequence is not None) \
            and _text_sig(_tokens(parsed.title or "")):
        return [Evidence("title", W_TITLE_LEAF, "folder names a book within a series")]
    return []


def ax_author_from_grouping(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:  # ctx: uniform signature
    """A GROUPING (classify_graph found its children are mostly title folders) is an author/series
    folder — vote author; a genuine single-series grouping is pulled to series by ax_series_ramp,
    and a known franchise (never an author) is suppressed."""
    from colophon.core.graph_classify import GROUPING
    if node.kind == GROUPING and not _is_known_franchise(node, ctx) and not _is_collection_name(node):
        return [Evidence("author", W_AUTHOR_GROUPING, "a folder of title subfolders (author/series grouping)")]
    return []


def ax_known_franchise(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder whose name exactly matches a user-declared franchise votes franchise — soft,
    competing evidence (weight 4.0) that beats a structural author guess but yields to a match
    (hard 10.0) or a manual override (hard 100.0), and to a genuine single-book title (5.0)."""
    from colophon.core.graph_resolve import _name_key
    display = ctx.known_franchises.get(_name_key(node.path.name))
    if display:
        return [Evidence("franchise", W_FRANCHISE, f"declared franchise '{display}'", value=display)]
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
            return [Evidence("author", W_TAG_AUTHOR_MATCH, f"a tagged author matches the folder name '{author}'",
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
        return [Evidence("author", min(W_CONSENSUS_MAX, 0.5 + 0.5 * top_n),
                         f"{top_n} book(s) tagged author '{display}'", value=display)]
    return []


_MATCH_SOURCES = frozenset({"audnexus", "audible", "hardcover", "openlibrary"})
_SERIES_COVERAGE = 0.6

# A value may vote in classification only when it is hard-sourced — a real tag, datafile, or match.
# A directory/filename/graphing value is a folder-name echo or a filename part-label (a chapter/
# section), which is exactly what classification is here to decide — so it must never vote.
_HARD_IDENTITY_PROV = frozenset(
    {"tag", "datafile", "audnexus", "audible", "hardcover", "openlibrary", "googlebooks", "manual"})


def ax_series_ramp(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """All/most books one HARD-sourced series with a sequence ramp AND the folder name resembles it
    -> series. A filename/directory series (e.g. a 'Chapter' part-label) is internal structure, not
    a real series, and casts no vote."""
    from colophon.core.graph_classify import _series_label
    from colophon.core.graph_resolve import _resembles
    all_books = ctx.books_by_folder.get(node.path, [])
    if not all_books:
        return []
    books = [b for b in all_books if b.provenance.get("series") in _HARD_IDENTITY_PROV]
    by_series = _distinct_series(books)
    if len(by_series) != 1:
        return []
    (_key, seqs), = by_series.items()
    if len(seqs) / len(all_books) < _SERIES_COVERAGE:
        return []
    ramp = sorted({s for s in seqs if s is not None})
    display = next(_series_label(b)[1] for b in books if _series_label(b))
    if len(ramp) >= 2 and _resembles(node.path.name, display):
        return [Evidence("series", W_SERIES_RAMP,
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


def _child_sequence_affix(name: str) -> SequenceAffix | None:
    """A child folder's sequence affix. Falls back to the folder-title parser for the
    `(Series Book #N) Title` and `#N - Title` folder forms that parse_sequence_affix does not
    read (it wants a leading `NN -` / `#N` bare form). A folder-parsed sequence is treated as a
    strong affix — explicit numbered-series notation, not an incidental leading number."""
    from colophon.core.sequence_affix import SequenceAffix, parse_sequence_affix
    aff = parse_sequence_affix(name)
    if aff is not None:
        return aff
    from colophon.core.folder_title import parse_folder_title
    parsed = parse_folder_title(name)
    if parsed.sequence is not None:
        return SequenceAffix(sequence=parsed.sequence, cleaned=parsed.title or name, confidence="strong")
    return None


def ax_numbered_siblings(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A folder whose child books carry sequence-number affixes ('02 - Yendi', '03 - Teckla', …)
    is a series ramp — structural series evidence that exists BEFORE any series field does (unlike
    ax_series_ramp, which needs the field). Additive: an attention trigger, a distinct-title ramp,
    and optional tag corroboration; the resolve() sum decides against the author-grouping vote."""
    books = ctx.books_by_folder.get(node.path, [])
    if not books:
        return []
    parsed: dict[str, SequenceAffix] = {}   # one entry per direct child name
    for b in books:
        name = _child_name(node.path, b)
        aff = _child_sequence_affix(name)
        if aff is not None:
            parsed.setdefault(name, aff)
    if not parsed:
        return []
    value = node.path.name
    evidence = [Evidence("series", W_NUMBERED_BASE, f"{len(parsed)} child name(s) carry a sequence number",
                         value=value)]
    nums = {a.sequence for a in parsed.values()}
    titles = {a.cleaned.casefold() for a in parsed.values()}
    has_strong = any(a.confidence == "strong" for a in parsed.values())
    corroborated = _series_tag_present(books)
    if len(parsed) >= 2 and len(nums) >= 2 and len(titles) >= 2 and (has_strong or corroborated):
        lo, hi = min(nums), max(nums)
        evidence.append(Evidence("series", W_NUMBERED_RAMP,
                                 f"numbered title ramp (seq {lo:g}-{hi:g}, distinct titles)",
                                 value=value))
    if corroborated:
        evidence.append(Evidence("series", W_NUMBERED_TAG, "child books carry a series tag", value=value))
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
            return [Evidence("author", W_MATCH, f"matched book(s) author '{author}' == folder name",
                             hard=True, value=author)]
    return []


def ax_manual_override(node: DirectoryNode, ctx: _Ctx) -> list[Evidence]:
    """A persisted user classification settles the node (hard), whatever kind they chose."""
    ov = ctx.overrides.get(str(node.path))
    if ov is None:
        return []
    return [Evidence(ov.kind, W_MANUAL, "you classified this folder", hard=True, value=ov.value)]


# The axioms are independent, pure, order-insensitive votes; resolve() sums them. Some are DESIGNED
# to stack on the same signal: ax_artist_consensus (the tagged books agree on an author) and
# ax_tag_author_match (a tagged author equals the folder name) both fire when a folder's tag authors
# agree AND match its name — the folder-name agreement deliberately reinforces the consensus.
_AXIOMS = (
    ax_manual_override, ax_matched_identity,          # hard
    ax_artist_consensus, ax_tag_author_match,         # author (name-bearing); may stack (see above)
    ax_leaf_title, ax_folder_title_shape,             # title (book-identity leaf)
    ax_author_structure, ax_author_from_grouping, ax_known_franchise,
    ax_numbered_siblings, ax_series_ramp,                                                # author/series/franchise (structural)
    ax_container_shape, ax_bucket_word,               # container
)


def _build_ctx(graph: Graph, root: Path, overrides: dict[str, NodeOverride],
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
    graph: Graph, books: list[BookUnit], *, root: Path, overrides: dict[str, NodeOverride],
    known_franchises: dict[str, str] | None = None, directory_scheme: str = "",
    filename_template: str = "",
    classify_only: set[str] | None = None,
) -> None:
    """Classify every directory node from accumulated axiom evidence, write the result onto the node,
    then fill empty/weak-author books from the nearest author node (GRAPHING).

    Ordering contract: `books` must already be through IDENTIFY. Several axioms read book fields and
    their provenance (e.g. ax_leaf_title inspects `book.title`/`book.provenance['title']`), so running
    this before IDENTIFY would classify against un-derived identity. `plan_scan_graph` guarantees the
    order (identify → classify_nodes).

    When `classify_only` is given, only nodes whose id is in the set are reclassified; every other
    ("frozen" spine) node keeps its existing kind/author. `_fill_down` still reads all nodes, so a
    frozen author node with a concrete `.author` is recorded as evidenced and can be inherited from."""
    ctx = _build_ctx(graph, root, overrides, known_franchises or {}, directory_scheme)
    evidenced: dict[str, bool] = {}
    for node in graph.directories.values():
        if classify_only is not None and node.id not in classify_only:
            # Frozen (spine) node: keep its restored classification; still record whether it carries a
            # concrete author value so _fill_down treats it as an evidenced author when appropriate.
            evidenced[node.id] = bool(node.author) if node.kind == "author" else False
            continue
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
    _fill_down(graph, books, evidenced, root=root, author_depth=ctx.author_depth,
               filename_template=filename_template)
    _fill_series_ramp(graph, books, root=root)
    _fill_title_corroboration(books)
    _fill_identity_confidence(graph, books, root=root)


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
    """For a book under a folder classified `series`, stamp series name + sequence when it has no
    stronger series. The sequence comes from the book's own folder's explicit book number
    ("(Series Book #N)" / "#N -"), falling back to the child-name affix ramp ("02 - Yendi") for a
    plain numbered shelf. GRAPHING provenance; a tag/datafile/match/manual series is never touched.
    Title affix-cleaning is NOT done here — the role-driven weak stage (`identify_weak`) owns the title."""
    from colophon.core.folder_title import parse_folder_title
    from colophon.core.metadata_quality import is_structural_marker
    from colophon.core.models import Provenance, SeriesRef
    from colophon.core.sequence_affix import parse_sequence_affix
    fillable = WEAK_PROV | {Provenance.GRAPHING.value}
    for book in books:
        node = _nearest_series(graph, book.source_folder, root)
        if node is None or not node.kind_value or is_structural_marker(node.kind_value):
            continue
        seq = parse_folder_title(book.source_folder.name).sequence
        if seq is None:
            aff = parse_sequence_affix(_child_name(node.path, book))
            seq = aff.sequence if aff is not None else None
        if seq is None:
            continue
        if not book.series or book.provenance.get("series") in fillable:
            book.series = [SeriesRef(name=node.kind_value, sequence=seq)]
            book.provenance["series"] = Provenance.GRAPHING.value


_STRONG_ID_PROV = frozenset({"manual", "match"})   # authoritative: a user or a source named it
_TAG_ID_PROV = frozenset({"tag", "datafile"})       # the file itself says so


def _nearest_author(graph: Graph, folder: Path, root: Path) -> DirectoryNode | None:
    """The nearest ancestor (incl. `folder`) classified `author`, or None — walking to root."""
    cur = folder
    while True:
        node = graph.directories.get(DirectoryNode.id_for(cur))
        if node is not None and node.kind == "author":
            return node
        if cur == root or root not in cur.parents:
            return None
        cur = cur.parent


def _field_confidence(prov: str | None, node_conf: float) -> float:
    """Confidence in one identity field (0-1) given how it was sourced. A user/match value is
    authoritative; the file's own tags are strong; everything else (graph inference, folder, filename)
    leans on the confidence of the graph node that backs it."""
    if prov in _STRONG_ID_PROV:
        return 1.0
    if prov in _TAG_ID_PROV:
        return 0.9
    return node_conf


def book_identity_confidence(book: BookUnit, graph: Graph, root: Path) -> float:
    """A book's local-identification confidence (0-100): how sure we are, from the graph evidence and
    the book's own provenance, that we've correctly identified it — pre-match, distinct from the
    post-match `confidence`. The author axis dominates; a corroborating series adds a little. The
    title factor is driven by the corroboration verdict: a title that agrees with (or abstains
    against) the folder/filenames is neutral, one that contradicts them halves the score, a missing
    title discounts. Graph/folder-sourced fields inherit the confidence of the classifying node, so a
    book under a 0.9 author folder reads ~0.9 even with zero source matches — UNLESS the surviving
    author value is itself junk-shaped, which discounts it toward the review threshold (the score is a
    triage hint: below a threshold means get human eyes on it before tagging)."""
    if not (book.authors or book.series):
        return 0.0
    from colophon.core.metadata_quality import author_junk
    from colophon.core.title_corroborate import book_title_verdict
    a_node = _nearest_author(graph, book.source_folder, root)
    a = (_field_confidence(book.provenance.get("authors"), a_node.kind_confidence if a_node else 0.0)
         if book.authors else 0.0)
    # Honest triage: a junk-shaped author value cannot prop up the score however trusted its source (a
    # tag reads 0.9 flat), so a SURVIVING '1 of 8 X' / 'Author.-.Title' / '(5)' author drops the record
    # below the review threshold for human eyes. Subsumes the old title-shaped-author guard — a
    # title-shaped author is author_junk == 1.0.
    if book.authors:
        a *= 1.0 - max(author_junk(x) for x in book.authors)
    s_node = _nearest_series(graph, book.source_folder, root)
    s = (_field_confidence(book.provenance.get("series"), s_node.kind_confidence if s_node else 0.0)
         if book.series else 0.0)
    corroboration = 0.1 if (a > 0 and s > 0) else 0.0
    verdict = book.title_corroboration or book_title_verdict(book).verdict
    if not book.title:
        title_factor = 0.7
    elif verdict == "contradict":
        title_factor = 0.5
    else:
        title_factor = 1.0
    echo_factor = (0.5 if (len(book.authors) == 1 and collides_with_title(book.authors[0], book.title))
                   else 1.0)
    return round(min(1.0, max(a, s) + corroboration) * title_factor * echo_factor * 100)


def _fill_title_corroboration(books: list[BookUnit]) -> None:
    """Stamp each book's title-corroboration verdict and, on a contradiction, raise a passive
    METADATA_CONFLICT finding. Mutates no identity field — this slice only scores and flags. Runs
    last, once author/series/franchise are resolved, so the title residual subtracts a complete set
    of known entities (title-as-residual)."""
    from colophon.core.models import Finding, FindingCode, FindingSeverity
    from colophon.core.title_corroborate import book_title_verdict
    for book in books:
        tc = book_title_verdict(book)
        book.title_corroboration = tc.verdict
        if tc.verdict == "contradict" and not any(
            f.code == FindingCode.METADATA_CONFLICT for f in book.findings
        ):
            book.findings.append(Finding(
                code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN, detail=tc.evidence,
            ))


def _fill_identity_confidence(graph: Graph, books: list[BookUnit], *, root: Path) -> None:
    """Stamp each book's local-identification confidence from the now-classified graph."""
    for book in books:
        book.identity_confidence = book_identity_confidence(book, graph, root)


def _fill_down(graph: Graph, books: list[BookUnit], evidenced: dict[str, bool], *,
               root: Path, author_depth: int | None, filename_template: str = "") -> None:
    """Inherit an author into each empty/weak-author book, walking leaf->root. Prefer the nearest
    classified author node (evidence-named over a folder-name fallback, so an intermediate grouping
    can't shadow the real author); failing that, fall back to the folder at the directory scheme's
    author depth (the declared layout) — but never a folder classified franchise/series/container,
    whose name is not an author. Never overwrite a book's own hard (tag/datafile/match/manual)
    author."""
    from colophon.core.author_evidence import _SETTLE_PROV, resolve_author
    from colophon.core.filename_parser import compile_template, parse_filename
    from colophon.core.identity_tokens import leaf_folder_author
    from colophon.core.metadata_quality import author_junk
    from colophon.core.models import Finding, FindingCode, FindingSeverity, Provenance
    from colophon.core.normalize import normalize_key, proper_case_if_shouting
    from colophon.core.people import split_people
    from colophon.core.reconcile import _demote_numeric_author
    pattern = compile_template(filename_template) if filename_template else None
    non_author = {"franchise", "series", "container"}
    for book in books:
        prov = book.provenance.get("authors")
        if book.authors and prov in _SETTLE_PROV:
            continue
        # NOTE (load-bearing, temporary): the two guards below (`root_is_soft_author`, the `title`
        # exclusion via `bool(book.title)`) are what keep a standalone title folder from being named
        # an author. They compensate for the fact that `build_graph`'s internal `plan_scan` still
        # commits the weak (directory/filename) identity BEFORE classification, so a book already
        # carries its decomposed title here. Once that identity is deferred (build_graph runs only
        # the hard stage), classification would see no weak title and these guards can be simplified
        # or removed. Safe today: this loop only runs for books with an empty/weak author (the
        # early-continue above skips any hard/manual-authored book), so a tagged book is untouched.
        seen: list[DirectoryNode] = []          # classified-author ancestors, nearest first
        layout: DirectoryNode | None = None     # the ancestor at the scheme's author depth
        cur = book.source_folder
        while True:
            node = graph.directories.get(DirectoryNode.id_for(cur))
            if node is not None:
                # The scan root is a bucket path (e.g. "incoming"), so its name is never an author —
                # a lone title folder directly beneath it is a standalone book, not an authored one.
                # Only a hard-settled root author (a match/manual) may still name the author (a
                # single-author library rooted at the author's name).
                root_is_soft_author = cur == root and not node.kind_source
                if node.kind == "author" and node.author and not root_is_soft_author:
                    seen.append(node)
                # The layout fallback names the author from the folder at the scheme's author depth,
                # but never from a folder whose name is not an author: franchise/series/container. A
                # `title` folder is admitted only when its lone book has NO title of its own — a bare
                # "Author Name" folder whose file is untitled, where the folder name doubles as the
                # author (Root/Author/untitled.mp3). A title folder that decomposed into a real title
                # (a year/narrator shape, a filename title) is a standalone book, not an author, so it
                # is excluded — the role-driven weak stage owns its identity.
                # A manual title (the user reclassified this folder) is authoritative and never an
                # author; an auto title is admitted only when its book has no title of its own.
                exclude_title = node.kind == "title" and (node.kind_source == "manual" or bool(book.title))
                if (author_depth is not None and _depth(cur, root) == author_depth
                        and node.kind not in non_author and not exclude_title):
                    layout = node
            if cur == root or root not in cur.parents:
                break
            cur = cur.parent
        chosen = next((n for n in seen if evidenced.get(n.id)), seen[0] if seen else None)
        # A user-confirmed (manual) author folder is authoritative — assign verbatim, skip the ballot.
        if chosen is not None and chosen.kind_source == "manual" and chosen.author:
            if book.authors != [chosen.author]:
                book.authors = [chosen.author]
                book.provenance["authors"] = Provenance.MANUAL.value
            continue
        # Structural author signals for the ballot (proper-cased so a shouting folder name is tidy).
        classified = (proper_case_if_shouting(chosen.author)
                      if (chosen is not None and chosen.author) else None)
        adf = proper_case_if_shouting(layout.path.name) if layout is not None else None
        # The book's own leaf folder may declare its author via the `.-.` convention
        # (`Author.-.Title`) — the case the depth logic misses when there is no author-node ancestor.
        # Only supply it as a CORRECTIVE vote: if the committed author already names the same person
        # (any shared name), skip it so a well-formatted 'Robert A. Heinlein' is not churned down to the
        # leaf's 'Robert A Heinlein'. It fires only when the current author is genuinely different (a
        # title smuggled in as the author) or absent.
        leaf_author = leaf_folder_author(book.source_folder.name)
        if leaf_author and book.authors:
            cur_keys = {normalize_key(a) for a in book.authors}
            if any(normalize_key(p) in cur_keys for p in split_people(leaf_author)):
                leaf_author = None
        # Filename $Author (positional pattern parse).
        filename_author = None
        if pattern is not None and book.source_files:
            parsed = parse_filename(pattern, book.source_files[0].path.name)
            fields = _demote_numeric_author(parsed) if parsed else {}
            filename_author = fields.get("author")
        # Datafile authors are NOT re-read from disk here: the hard IDENTIFY stage already vetted the
        # sidecar (rejecting a container/uploader datafile for a split leaf via is_container_datafile)
        # and, if legitimate, committed it onto book.authors with `datafile` provenance — which the
        # tag/datafile skip above preserves. Re-reading the raw sidecar would resurrect a handle the
        # hard stage deliberately dropped, so the ballot's datafile vote stays empty.
        prior_authors, prior_prov = list(book.authors), book.provenance.get("authors")
        # The confidence boost applies only to an EVIDENCE-NAMED author node (its name is corroborated
        # by the books' own tags/matches), not a bare folder-name fallback — so a misfiled or
        # misspelled folder name stays at the floor and cannot overwrite a clean tag.
        node_evidenced = chosen is not None and bool(evidenced.get(chosen.id))
        r = resolve_author(book, author_depth_folder=adf, classified_author_name=classified,
                           classified_author_confidence=(chosen.kind_confidence if node_evidenced else 0.0),
                           datafile_authors=[], filename_author=filename_author,
                           sibling_consensus={}, leaf_folder_author=leaf_author)
        # Provenance of a "folder"-sourced win. resolve_author collapses the classified-author-node
        # and the raw author-depth folder into one "folder" source, so restore the intended tier here:
        #   - value UNCHANGED from the book's prior author -> the folder only corroborated; keep the
        #     original provenance tier (a confident author node now out-weighs an agreeing tag, but a
        #     tag/datafile/scheme that already named this author correctly must not churn to folder);
        #   - value CHANGED, inherited from a classified AUTHOR node -> GRAPHING, the graph named it;
        #   - otherwise the raw author-depth folder-name fallback stays DIRECTORY.
        if r.source == "folder" and book.provenance.get("authors") == Provenance.DIRECTORY.value:
            if book.authors == prior_authors and prior_prov:
                book.provenance["authors"] = prior_prov
            elif chosen is not None and classified is not None \
                    and book.authors == split_people(classified):
                book.provenance["authors"] = Provenance.GRAPHING.value
        # Conflict flag: the book's own embedded tag artist and the classified folder author name
        # DIFFERENT people (a misfiled folder, a narrator/misspelled tag) — both real (non-junk). We
        # compare the committed sources directly, not the transient ballot, so it fires the same on a
        # reload (where the ballot may carry only one side) as on a rebuild. Junk on either side is
        # noise we overcame, not a conflict. We cannot always pick right, so flag it for review.
        tag_artist = (book.source_files[0].tags.artist
                      if book.source_files and book.source_files[0].tags else None)
        # Compare PEOPLE-sets, not raw strings: a real conflict names a different person, not the same
        # people in a different format ('Clarke, Baxter' vs 'Clarke and Baxter') or a dropped co-author
        # ('McCaffrey & Scarborough' vs 'McCaffrey'). Suppress when the sets match or one contains the
        # other; flag only a genuine disagreement.
        tag_people = {normalize_key(p) for p in split_people(tag_artist or "")}
        folder_people = {normalize_key(p) for p in split_people(classified or "")}
        disagree = bool(tag_people and folder_people) and not (
            tag_people <= folder_people or folder_people <= tag_people)
        if (tag_artist and classified and author_junk(tag_artist) == 0 and author_junk(classified) == 0
                and disagree
                and not any(f.code == FindingCode.METADATA_CONFLICT and (f.detail or "").startswith("author:")
                            for f in book.findings)):
            book.findings.append(Finding(
                code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                detail=f"author: tag '{tag_artist}' vs folder '{classified}'",
            ))
