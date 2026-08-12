"""Weighted-evidence resolution for field VALUES - the value-analogue of `node_classify.resolve`.

Collect `FieldEvidence` votes; sum weight per normalized value; the top value wins with likelihood =
its share of total weight (identical math to `kind_confidence`). Hard evidence (manual/match) settles.
A blank or non-positive-weight (junk-penalized) candidate never counts toward the tally, but is kept
in `evidence` for the readout."""

from __future__ import annotations

from dataclasses import dataclass, field

from colophon.core.ballot import tally


@dataclass(frozen=True)
class FieldEvidence:
    value: str
    weight: float
    source: str
    reason: str
    hard: bool = False


@dataclass(frozen=True)
class ResolvedField:
    value: str | None
    likelihood: float
    evidence: list[FieldEvidence] = field(default_factory=list)
    source: str | None = None      # the winning value's strongest contributing source


def _key(value: str) -> str:
    """Conservative grouping key: casefold + collapse whitespace."""
    return " ".join(value.casefold().split())


def resolve_field(candidates: list[FieldEvidence]) -> ResolvedField:
    votes = [c for c in candidates if c.value and c.value.strip() and c.weight > 0]
    if not votes:
        return ResolvedField(None, 0.0, list(candidates))
    hard = [c for c in votes if c.hard]
    pool = hard or votes
    display: dict[str, str] = {}
    for c in pool:
        display.setdefault(_key(c.value), c.value)
    t = tally([(_key(c.value), c.weight) for c in pool])
    best_source = max((c for c in pool if _key(c.value) == t.winner), key=lambda c: c.weight).source
    return ResolvedField(display[t.winner], t.share, list(candidates), source=best_source)
