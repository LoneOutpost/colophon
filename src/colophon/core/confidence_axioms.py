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

from colophon.core.models import EmbeddedTags, Provenance

# --- Tunable constants. Everything the scoring can be re-composed with lives in this block. ---

W_MANUAL = 1.00          # the user said so
W_MATCH = 0.85           # an external source: independent of local labelling, still fallible
W_FOLDER = 0.75          # most visible, most consistent in bulk dumps
W_FILENAME = 0.65        # visible, noisier than folders
W_TAG_BASE = 0.30        # a bare tag: present, barely evidenced
W_TAG_COMPLETE = 0.45    # added at full tag completeness, so a rich tag reaches folder-level trust
W_GRAPH_FACTOR = 0.80    # graph inference is one step removed from a direct observation

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


def source_weight(prov: str | None, tags: EmbeddedTags | None, node_confidence: float) -> float:
    """What one source's claim is worth, in [0, 1].

    Filenames and folders outrank tags because they are visible: a wrong folder name gets noticed and
    fixed, a wrong tag stays buried for years.
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
    return round(node_confidence * W_GRAPH_FACTOR, 4)
