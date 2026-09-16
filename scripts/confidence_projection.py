"""Project the new identity-confidence scoring over an existing colophon DB and report the band
shift, without a re-scan. Stored scores are not rewritten: this answers "what would it say?" so the
constants in core/confidence_axioms.py can be tuned against a real library before anything ships.

Usage:
    uv run python scripts/confidence_projection.py [DB]
    uv run python scripts/confidence_projection.py [DB] --explain 20
    uv run python scripts/confidence_projection.py [DB] --threshold 60
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from colophon.core.confidence_axioms import ScoreCtx, score_identity
from colophon.core.models import BookUnit

BANDS = ((1, "none (0)"), (25, "very low (1-24)"), (50, "low (25-49)"),
         (70, "fair (50-69)"), (80, "good (70-79)"), (101, "high (80+)"))

LIMITS = """\
Limits of this projection:
  - It scores from each book's STORED fields only. It has no graph, so a graph-resolved author or
    series casts no vote here; a real re-scan will score those books somewhat HIGHER than shown.
  - It measures the DB you point it at. A single-genre dump is not your general population.
  - Junk and agreement verdicts are the pipeline's own heuristics, not ground truth."""


def band(value: float) -> str:
    for ceiling, label in BANDS:
        if value < ceiling:
            return label
    return BANDS[-1][1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?",
                        default=str(Path.home() / ".local/share/colophon/colophon.db"))
    parser.add_argument("--explain", type=int, default=0,
                        help="print the signal breakdown for the N biggest movers")
    parser.add_argument("--threshold", type=float, default=60.0,
                        help="the identity threshold to report review counts against")
    args = parser.parse_args()
    if not Path(args.db).is_file():
        raise SystemExit(f"no database at {args.db}")

    con = sqlite3.connect(args.db)
    try:
        rows = [json.loads(d) for (d,) in con.execute("SELECT data FROM book_units")]
    finally:
        con.close()

    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    movers: list[tuple[float, str, float, float, list]] = []
    was_over = now_over = 0
    for raw in rows:
        book = BookUnit.model_validate(raw)
        old = book.identity_confidence
        result = score_identity(book, ScoreCtx())
        before[band(old)] += 1
        after[band(result.score)] += 1
        was_over += old >= args.threshold
        now_over += result.score >= args.threshold
        movers.append((old - result.score, book.title or "(untitled)", old, result.score,
                       result.signals))

    total = len(rows)
    print(f"DB {args.db}   books {total}\n")
    print(f"{'band':24} {'before':>8} {'after':>8}")
    for _c, label in BANDS:
        print(f"  {label:22} {before[label]:>8} {after[label]:>8}")
    print(f"\nat threshold {args.threshold:.0f}: "
          f"{was_over} of {total} were at or above, {now_over} now are "
          f"({total - now_over} would need review)")

    if args.explain:
        print(f"\n== {args.explain} largest drops ==")
        for drop, title, old, new, signals in sorted(movers, reverse=True)[:args.explain]:
            print(f"\n  {title[:56]}   {old:.0f} -> {new:.0f}  ({-drop:+.0f})")
            for s in signals:
                print(f"       {s.name:18} {s.points:>4}  {s.detail}")
    print(f"\n{LIMITS}")


if __name__ == "__main__":
    main()
