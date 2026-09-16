"""Scoring axioms: the axiom family that quantifies the QUALITY of identified metadata.

The shape/provenance family in `node_classify` decides what a piece of metadata IS and where it came
from. This family asks how good what we found actually is, and it shares that family's shape
deliberately: a uniform signature, weights as named constants, a human-readable reason on every
contribution, and one registry listing the active set.

An axiom returns `Support` (weight toward an identity axis) or `Cap` (permission to exceed the local
ceiling). An axiom with no applicable evidence returns `[]`, which is also why the score sharpens as
the pipeline fills the graph in without needing a per-phase code path.
"""

from __future__ import annotations

from dataclasses import dataclass

from colophon.core.ballot import tally
from colophon.core.folder_title import parse_folder_title
from colophon.core.metadata_quality import author_junk, is_junk_title, is_title_shaped_author
from colophon.core.models import BookUnit, ConfidenceSignal, EmbeddedTags, Provenance
from colophon.core.normalize import normalize_key
from colophon.core.people import split_people

# --- Tunable constants. Everything the scoring can be re-composed with lives in this block. ---

W_MANUAL = 1.00          # the user said so
W_MATCH = 0.85           # an external source: independent of local labelling, still fallible
W_FOLDER = 0.75          # most visible, most consistent in bulk dumps
W_FILENAME = 0.65        # visible, noisier than folders
W_TAG_BASE = 0.30        # a bare tag: present, barely evidenced
W_TAG_COMPLETE = 0.45    # added at full tag completeness, so a rich tag reaches folder-level trust
# A graph-resolved value and a folder-name guess are the SAME evidence — the directory structure —
# resolved with different precision. The graph's advantage is picking the RIGHT ancestor, not being a
# stronger source, so they carry the same weight. Weighting them differently made a book's score
# depend on how completely the graph happened to be derived: a scoped re-derive scored 68 where a
# whole-root re-derive scored 70, for the same book.
W_GRAPH = W_FOLDER
# What complete local corroboration is worth: the directory structure (W_FOLDER) plus one
# corroborating source (a reasonably complete tag). Two strong independent sources sum to 1.40 and
# therefore exceed it, which is correct — they are more than fully corroborated. Calibrated against a
# real 230-book library: at 1.40 a clean tagless Author/Title library scored 54 and sent every book
# to review, which is its own kind of dishonesty; at 1.20 such a book reaches 62 while an
# uncorroborated weak tag still scores 25.
FULL_SUPPORT = 1.20

CEIL_LOCAL = 0.70    # nothing drawn from the library's own labelling can exceed this
CEIL_MATCH = 0.95    # an external source is independent, never infallible
CEIL_MANUAL = 1.00   # the user is the authority

AXIS_AUTHOR = 0.60   # the author axis stays dominant, as it is today
AXIS_TITLE = 0.40    # the replacement for max(a, s)
SERIES_BONUS = 0.05  # series adds only; it never subtracts

# Provenance values that name an EXTERNAL source. `Provenance` emits provider names, never the
# string "match" that node_classify's _STRONG_ID_PROV tested for, which is why a matched field used
# to score as a graph inference.
MATCH_PROV = frozenset({
    Provenance.AUDNEXUS.value, Provenance.AUDIBLE.value, Provenance.HARDCOVER.value,
    Provenance.OPENLIBRARY.value, Provenance.GOOGLEBOOKS.value,
})
TAG_PROV = frozenset({Provenance.TAG.value, Provenance.DATAFILE.value})

# The tag fields a deliberate tagger fills in. asin/isbn count once: they are the same claim.
_COMPLETENESS_FIELDS = ("title", "album", "artist", "narrator", "series",
                        "year", "genre", "description")


def tag_completeness(tags: EmbeddedTags | None) -> float:
    """How completely this file is tagged, in [0, 1].

    A file carrying album, artist, narrator, year, a real description and an identifier was tagged
    deliberately by someone who cared; one with a single mangled title field was tagged by a script
    that did not. Same provenance label, very different trustworthiness — which is why tag weight is
    measured here rather than assumed as a constant.
    """
    if tags is None:
        return 0.0
    filled = sum(1 for name in _COMPLETENESS_FIELDS if getattr(tags, name, None) is not None)
    if tags.asin is not None or tags.isbn is not None:
        filled += 1
    return round(filled / (len(_COMPLETENESS_FIELDS) + 1), 4)


def source_weight(prov: str | None, tags: EmbeddedTags | None) -> float:
    """What one source's claim is worth, in [0, 1].

    Filenames and folders outrank tags because they are visible: a wrong folder name gets noticed and
    fixed, a wrong tag stays buried for years.

    A graph-resolved value carries a FLAT weight, deliberately not the classifying node's
    `kind_confidence`. That confidence reflects how much of the tree the classifier had just
    examined, so a scoped re-derive and a whole-root re-derive produce different numbers for the same
    book — scan scope is an artifact, not evidence about the book. What the graph RESOLVED is the
    evidence; how sure the classifier was is its own business.
    """
    if prov == Provenance.MANUAL.value:
        return W_MANUAL
    if prov in MATCH_PROV:
        return W_MATCH
    if prov == Provenance.DIRECTORY.value:
        return W_FOLDER
    if prov == Provenance.FILENAME.value:
        return W_FILENAME
    if prov in TAG_PROV:
        return round(W_TAG_BASE + W_TAG_COMPLETE * tag_completeness(tags), 4)
    if prov == Provenance.GRAPHING.value:
        return W_GRAPH
    return 0.0


def _first_tags(book: BookUnit) -> EmbeddedTags | None:
    return book.source_files[0].tags if book.source_files else None


def _is_usable(axis: str, value: str | None, book: BookUnit) -> bool:
    """A junk value does not get to vote. Excluding it here subsumes the old echo factor: an author
    that echoes the book's title is `is_title_shaped_author`, not a second rule."""
    if not value or not value.strip():
        return False
    if axis == "author":
        return author_junk(value) == 0.0 and not is_title_shaped_author(value, book.title)
    if axis == "title":
        return not is_junk_title(value)
    return True


def axis_candidates(book: BookUnit, axis: str, node_value: str | None
                    ) -> list[tuple[str, float, str]]:
    """Every source's claim for one axis, as (value, weight, source_name).

    Folder and filename are two independent votes, not one: they are produced by different acts and
    disagree in informative ways. In bulk dumps the folder is typically the steadier of the two.
    """
    tags = _first_tags(book)
    folder = book.source_folder
    out: list[tuple[str, float, str]] = []

    def add(value: str | None, prov: str, name: str) -> None:
        # `value` is narrowed here rather than inside _is_usable, which a type checker cannot see
        # through: the blank/None rejection lives there, and this keeps both readers honest.
        if not value or not _is_usable(axis, value, book):
            return
        weight = source_weight(prov, tags)
        if weight > 0:
            out.append((value.strip(), weight, name))

    if tags is not None:
        add({"author": tags.artist, "title": tags.album, "series": tags.series}.get(axis),
            Provenance.TAG.value, "tag")
    if axis in ("author", "series") and node_value:
        # The graph already resolved which ancestor names this book's author/series and what it is
        # called. Prefer that over guessing from the path: a graph-confirmed classification is
        # corroborated across siblings, and the guess below assumes a two-level Author/Title layout
        # that a single-level library does not have.
        add(node_value, Provenance.GRAPHING.value, "graph")
    elif folder is not None:
        if axis == "author":
            add(folder.parent.name, Provenance.DIRECTORY.value, "folder")
        elif axis == "title":
            add(parse_folder_title(folder.name).title or folder.name,
                Provenance.DIRECTORY.value, "folder")
    if axis == "title":
        for sf in book.source_files[:1]:
            add(parse_folder_title(sf.path.stem).title or sf.path.stem,
                Provenance.FILENAME.value, "filename")

    committed = {"author": book.authors[0] if book.authors else None,
                 "title": book.title,
                 "series": book.series[0].name if book.series else None}.get(axis)
    prov = book.provenance.get({"author": "authors", "title": "title", "series": "series"}[axis])
    if prov in MATCH_PROV or prov == Provenance.MANUAL.value:
        add(committed, prov, "match" if prov in MATCH_PROV else "manual")
    return out


def _agreement_key(value: str) -> str:
    """The bucket key two sources must share to count as agreeing.

    `normalize_key` turns an apostrophe into a SPACE, so "Sharpe's Siege" and the apostrophe-stripped
    "Sharpes Siege" a tagger or filesystem routinely produces land in different buckets and read as a
    contradiction. Titles carrying an apostrophe are common enough that this would depress scores
    across a whole library. Dropping the apostrophe before keying fixes it HERE, in the scoring
    ballot, rather than in `normalize_key` — that is the shared entity-dedup key for the whole
    application, and loosening it could merge entities that must stay distinct.
    """
    return normalize_key(value.replace("'", "").replace("\u2019", ""))


def _candidate_key(value: str, committed_key: str, axis: str) -> str:
    """The bucket a source's value falls into, given what the book committed to.

    On the author axis a joint credit ("McCaffrey & Scarborough") agrees with a single committed
    author named within it: the source is naming MORE people, not a different person. Restricted to
    the author axis because splitting a title on " and " would invent agreement that is not there.
    """
    if _agreement_key(value) == committed_key:
        return committed_key
    if axis == "author" and any(_agreement_key(p) == committed_key for p in split_people(value)):
        return committed_key
    return _agreement_key(value)


def axis_support(candidates: list[tuple[str, float, str]], committed: str | None,
                 axis: str = "title") -> float:
    """Support for one axis, in [0, 1]: the summed weight of the sources that agree with the value
    the book actually committed to, normalised against what full local corroboration is worth.

    Support is measured against `committed`, not against whichever value happens to win the ballot.
    Confidence answers "how well is THIS identity evidenced", so a book whose folder and filename
    outvote its committed author must score LOW — under a winner-based reading it would score well
    while being wrong, which is the same dishonesty this family exists to remove, one layer down.
    A book with nothing committed on this axis has nothing to support, and scores 0.

    The agreeing weight is summed, never `tally().share`: share reads 1.0 whenever a single source
    votes, because a lone voter trivially agrees with itself, so a share-based model would hand an
    uncorroborated tag full credit.
    """
    if not candidates or not committed or not committed.strip():
        return 0.0
    key = _agreement_key(committed)
    result = tally([(_candidate_key(value, key, axis), weight) for value, weight, _name in candidates])
    agreeing = result.totals.get(key, 0.0)
    return round(min(1.0, agreeing / FULL_SUPPORT), 4)


# --- The axiom family. Each returns Support (weight toward one axis) or Cap (permission to exceed
# the local ceiling) — never both meanings folded into one number. ---


@dataclass(frozen=True)
class Support:
    """Weight toward one identity axis, with the reason it was granted."""

    axis: str        # "author" | "title" | "series"
    weight: float
    reason: str


@dataclass(frozen=True)
class Cap:
    """Permission to exceed the local ceiling. A ceiling is a constraint, not weight: collapsing it
    into arithmetic is how a flat 0.9 plus a 0.1 bonus used to reach certainty."""

    ceiling: float
    reason: str


@dataclass(frozen=True)
class ScoreCtx:
    """Everything an axiom may read besides the book itself. The caller resolves graph nodes and
    passes their confidences in, so axioms stay pure and testable without building a Graph."""

    author_node_value: str | None = None
    series_node_value: str | None = None


def committed_value(book: BookUnit, axis: str) -> str | None:
    """The value the book actually holds on this axis — what confidence is being measured about."""
    if axis == "author":
        return book.authors[0] if book.authors else None
    if axis == "series":
        return book.series[0].name if book.series else None
    return book.title


def _support_for(book: BookUnit, axis: str, node_value: str | None) -> list[Support]:
    committed = committed_value(book, axis)
    if not committed:
        return []
    candidates = axis_candidates(book, axis, node_value)
    weight = axis_support(candidates, committed, axis)
    if weight <= 0:
        return []
    key = _agreement_key(committed)
    agreeing = sorted({name for value, _w, name in candidates
                       if _candidate_key(value, key, axis) == key})
    return [Support(axis, weight, f"{axis} supported by {', '.join(agreeing)}")]


def cf_author_support(book: BookUnit, ctx: ScoreCtx) -> list[Support | Cap]:
    """The author axis, corroborated across every source that names one."""
    return list(_support_for(book, "author", ctx.author_node_value))


def cf_title_support(book: BookUnit, ctx: ScoreCtx) -> list[Support | Cap]:  # ctx: uniform axiom signature
    """The title axis. Replaces `max(a, s)`: an unevidenced title now costs part of the score
    instead of being ignored whenever the author happened to be strong."""
    return list(_support_for(book, "title", None))


def cf_series_support(book: BookUnit, ctx: ScoreCtx) -> list[Support | Cap]:  # ctx: uniform axiom signature
    """The series axis. Adds only — see the engine; a book legitimately without a series must not be
    penalised for a field it should not have."""
    return list(_support_for(book, "series", ctx.series_node_value))


# The fields whose provenance says something about IDENTITY. A provider filling in genres or a
# subtitle has not corroborated who wrote the book or what it is called.
IDENTITY_FIELDS = ("title", "authors", "series")


def cf_match_ceiling(book: BookUnit, ctx: ScoreCtx) -> list[Support | Cap]:  # ctx: uniform axiom signature
    """An external source is independent of the library's own labelling, so it lifts the ceiling —
    but never to certainty, because a provider can return the wrong edition.

    Only a match on an IDENTITY field counts. Measured against a real library, keying on ANY matched
    field handed the raised ceiling to 100 of 167 books whose only match was `genres`, `tags` or
    `subtitle` — peripheral data that says nothing about whether the book is correctly identified.
    That is the same false certainty this family exists to remove, arriving through a side door.
    """
    matched = [f for f in IDENTITY_FIELDS if book.provenance.get(f) in MATCH_PROV]
    if matched:
        return [Cap(CEIL_MATCH, f"an external source match backs the {', '.join(matched)}")]
    return []


def cf_manual(book: BookUnit, ctx: ScoreCtx) -> list[Support | Cap]:  # ctx: uniform axiom signature
    """A confirmed book is as settled as anything gets: the user is the authority, so confirmation
    grants full support on both axes AND lifts the ceiling. Granting only the ceiling would give a
    book permission to score 100 with no evidence to get there."""
    if not book.manually_confirmed:
        return []
    return [
        Cap(CEIL_MANUAL, "manually confirmed"),
        Support("author", 1.0, "manually confirmed"),
        Support("title", 1.0, "manually confirmed"),
    ]


SCORING_AXIOMS = [
    cf_author_support,
    cf_title_support,
    cf_series_support,
    cf_match_ceiling,
    cf_manual,
]


@dataclass(frozen=True)
class ScoredIdentity:
    score: float
    signals: list[ConfidenceSignal]


def score_identity(book: BookUnit, ctx: ScoreCtx) -> ScoredIdentity:
    """Run the scoring axiom family and combine what it returns.

    Support sums per axis; the ceiling is the HIGHEST any axiom grants, defaulting to the local
    ceiling, and it is applied last so no bonus can buy past a limit that exists for a different
    reason.
    """
    contributions: list[Support | Cap] = []
    for axiom in SCORING_AXIOMS:
        contributions.extend(axiom(book, ctx))

    per_axis: dict[str, float] = {}
    signals: list[ConfidenceSignal] = []
    ceiling = CEIL_LOCAL
    for item in contributions:
        if isinstance(item, Support):
            per_axis[item.axis] = per_axis.get(item.axis, 0.0) + item.weight
            signals.append(ConfidenceSignal(
                name=f"{item.axis}_support", points=round(item.weight * 100), detail=item.reason))
        else:
            ceiling = max(ceiling, item.ceiling)
            signals.append(ConfidenceSignal(
                name="ceiling", points=round(item.ceiling * 100), detail=item.reason))

    core = AXIS_AUTHOR * min(1.0, per_axis.get("author", 0.0)) \
        + AXIS_TITLE * min(1.0, per_axis.get("title", 0.0))
    if per_axis.get("series", 0.0) > 0:
        core += SERIES_BONUS
    score = float(round(min(core, ceiling) * 100))
    signals.append(ConfidenceSignal(
        name="ceiling_applied", points=round(ceiling * 100),
        detail=f"evidence allows at most {round(ceiling * 100)}"))
    return ScoredIdentity(score=score, signals=signals)
