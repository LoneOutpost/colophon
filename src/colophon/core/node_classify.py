"""Weighted-evidence node classifier: pure axioms emit votes, the resolver tallies them into a
Classification (kind + value + confidence + source + evidence). Soft votes accumulate a mutable
confidence store; hard evidence (a match or a manual confirmation) settles the node. Replaces the
imperative resolve_graph_authors/hint_grouping_kinds passes."""

from __future__ import annotations

from dataclasses import dataclass, field

# Fixed candidate order — used to break exact soft ties deterministically.
_KIND_ORDER = ("author", "series", "franchise", "container")


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
