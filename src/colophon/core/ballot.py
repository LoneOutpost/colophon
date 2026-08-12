"""The shared weighted-election primitive. Sum weight per bucket key; the heaviest bucket wins with a
likelihood = its share of total weight. The single tally behind every ballot box — field-value
resolution, node-kind classification, and (later) Book-bucket grouping. See
tasks/2026-08-11-ballot-engines-architecture.md."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Tallied:
    winner: str | None            # winning bucket key; None only when there are no votes
    share: float                  # round(winner_total / grand_total, 2); 0.0 when total == 0
    totals: dict[str, float]      # summed weight per bucket key (kept for the future inspector UI)


def tally(votes: list[tuple[str, float]], *, order: Sequence[str] | None = None) -> Tallied:
    """Weighted election. `votes` is a list of (bucket_key, weight); callers pre-filter (drop blanks /
    non-positive weights) and pre-select the pool (e.g. hard-only) before calling.

    Sums weight per key. Winner = the key with the greatest total; ties broken by:
      - `order is None`  -> the key's STRONGEST single vote weight  (field-value semantics)
      - `order` given    -> earliest position in `order`            (kind semantics; universe = order)
    `share` = winner_total / grand_total, rounded to 2 (0.0 when total == 0). Empty `votes` -> winner
    None, share 0.0."""
    if not votes:
        return Tallied(None, 0.0, {})
    totals: dict[str, float] = {}
    strongest: dict[str, float] = {}
    for key, w in votes:
        totals[key] = totals.get(key, 0.0) + w
        strongest[key] = max(strongest.get(key, 0.0), w)
    total = sum(totals.values())
    if order is None:
        winner = max(totals, key=lambda k: (totals[k], strongest[k]))
    else:
        winner = max(order, key=lambda k: (totals.get(k, 0.0), -list(order).index(k)))
    share = round(totals.get(winner, 0.0) / total, 2) if total else 0.0
    return Tallied(winner, share, totals)
