"""Standing quality report for a colophon library DB: the same yardstick every identification slice
can be measured against, so a later change cannot silently undo an earlier one's gains.

Unlike the per-slice acceptance gates beside it (known_series_dryrun, title_essence_dryrun), this
measures the *committed* state of a library rather than what one component would change. Write a
baseline before a slice, re-run after, and diff.

Every judgement here delegates to the pipeline's own detectors in core.metadata_quality, so the
report and the pipeline can never drift into disagreeing about what counts as junk.

Usage:
    uv run python scripts/library_quality.py [DB]
    uv run python scripts/library_quality.py [DB] --json before.json
    uv run python scripts/library_quality.py [DB] --baseline before.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from colophon.core.metadata_quality import (
    author_junk,
    is_junk_title,
    is_structural_marker,
    is_title_shaped_author,
    title_junk,
)

# What this report does NOT see. Printed on every run: a number whose blind spot is hidden is worse
# than no number, and these metrics are only as representative as the library they are run against.
LIMITS = """\
Limits of this report:
  - It measures the DB you point it at. A single genre dump is not your general population.
  - Junk/structural verdicts are the pipeline's own heuristics, not ground truth. A drop in "junk
    titles" means the detectors fire less often, which is evidence of improvement, not proof.
  - It reads committed values only. A book nothing has identified yet counts as empty, not as wrong.
  - Books flagged skipped or missing are excluded from the quality rates and counted separately."""

CONF_BUCKETS = ((0.0, "none (0)"), (50.0, "low (0-50)"), (80.0, "fair (50-80)"), (101.0, "good (80+)"))


def _bucket(value: float) -> str:
    for ceiling, label in CONF_BUCKETS:
        if value < ceiling or (ceiling == 0.0 and value == 0.0):
            return label
    return CONF_BUCKETS[-1][1]


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def collect(db: str) -> dict[str, Any]:
    """Read every book_unit and reduce it to the standing quality metrics."""
    if not Path(db).is_file():
        raise SystemExit(f"no database at {db}\n"
                         "A scratch DB under /tmp does not survive a reboot; re-import or pass the "
                         "path to a library DB.")
    con = sqlite3.connect(db)
    try:
        rows = [json.loads(d) for (d,) in con.execute("SELECT data FROM book_units")]
    finally:
        con.close()

    counts: Counter[str] = Counter()
    states: Counter[str] = Counter()
    confidence: Counter[str] = Counter()
    findings: Counter[str] = Counter()
    title_prov: Counter[str] = Counter()
    author_prov: Counter[str] = Counter()
    title_scores: list[float] = []
    author_scores: list[float] = []

    graded = 0
    for u in rows:
        counts["books"] += 1
        if u.get("skipped"):
            counts["skipped"] += 1
            continue
        if u.get("missing"):
            counts["missing"] += 1
            continue
        graded += 1

        states[str(u.get("state") or "unknown")] += 1
        confidence[_bucket(float(u.get("identity_confidence") or 0.0))] += 1
        if u.get("manually_confirmed"):
            counts["manually_confirmed"] += 1

        acknowledged = set(u.get("acknowledged_findings") or [])
        for f in u.get("findings") or []:
            code = f.get("code") if isinstance(f, dict) else str(f)
            if code and code not in acknowledged:
                findings[str(code)] += 1

        provenance = u.get("provenance") or {}
        title_prov[str(provenance.get("title") or "none")] += 1
        author_prov[str(provenance.get("authors") or "none")] += 1

        title = (u.get("title") or "").strip()
        if not title:
            counts["title_empty"] += 1
        else:
            if is_junk_title(title):
                counts["title_junk"] += 1
            if is_structural_marker(title):
                counts["title_structural"] += 1
            title_scores.append(title_junk(title))

        authors = [a for a in (u.get("authors") or []) if str(a).strip()]
        if not authors:
            counts["author_empty"] += 1
        else:
            first = str(authors[0])
            if is_structural_marker(first):
                counts["author_structural"] += 1
            if is_title_shaped_author(first, title or None):
                counts["author_title_shaped"] += 1
            author_scores.append(author_junk(first))

        if not (u.get("series") or []):
            counts["series_absent"] += 1

    return {
        "db": db,
        "graded": graded,
        "counts": dict(counts),
        "states": dict(states),
        "identity_confidence": dict(confidence),
        "open_findings": dict(findings),
        "title_provenance": dict(title_prov),
        "author_provenance": dict(author_prov),
        "mean_title_junk": _mean(title_scores),
        "mean_author_junk": _mean(author_scores),
    }


def _rate(n: int, total: int) -> str:
    return f"{n:6d}  ({100.0 * n / total:5.1f}%)" if total else f"{n:6d}"


def _section(title: str, mapping: dict[str, Any], total: int) -> None:
    print(f"\n== {title} ==")
    for key, value in sorted(mapping.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"   {key:28} {_rate(value, total)}")
    if not mapping:
        print("   (none)")


def report(m: dict[str, Any]) -> None:
    total = m["graded"]
    counts = m["counts"]
    print(f"DB {m['db']}")
    print(f"books {counts.get('books', 0)} | graded {total} "
          f"| skipped {counts.get('skipped', 0)} | missing {counts.get('missing', 0)}")

    print("\n== identity quality ==")
    for label, key in (
        ("title empty", "title_empty"),
        ("title junk", "title_junk"),
        ("title structural marker", "title_structural"),
        ("author empty", "author_empty"),
        ("author structural marker", "author_structural"),
        ("author is title-shaped", "author_title_shaped"),
        ("series absent", "series_absent"),
        ("manually confirmed", "manually_confirmed"),
    ):
        print(f"   {label:28} {_rate(counts.get(key, 0), total)}")
    print(f"   {'mean title junk score':28} {m['mean_title_junk']:>8}")
    print(f"   {'mean author junk score':28} {m['mean_author_junk']:>8}")

    _section("local identification confidence", m["identity_confidence"], total)
    _section("title provenance", m["title_provenance"], total)
    _section("author provenance", m["author_provenance"], total)
    _section("open findings", m["open_findings"], total)
    _section("phase state", m["states"], total)
    print(f"\n{LIMITS}")


def _flatten(m: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {"graded": m["graded"]}
    for group in ("counts", "identity_confidence", "open_findings", "title_provenance",
                  "author_provenance", "states"):
        for key, value in m[group].items():
            flat[f"{group}.{key}"] = value
    flat["mean_title_junk"] = m["mean_title_junk"]
    flat["mean_author_junk"] = m["mean_author_junk"]
    return flat


# Metrics where a smaller number is the improvement. Everything else (confirmations, provenance
# strength, graded totals) is reported as a plain delta without a verdict.
_LOWER_IS_BETTER = ("counts.title_", "counts.author_", "counts.series_absent",
                    "open_findings.", "mean_title_junk", "mean_author_junk")


def diff(before: dict[str, Any], after: dict[str, Any]) -> None:
    a, b = _flatten(before), _flatten(after)
    print(f"baseline {before['db']}  ->  current {after['db']}")
    print(f"graded {before['graded']} -> {after['graded']}\n")
    moved = False
    for key in sorted(set(a) | set(b)):
        old, new = a.get(key, 0), b.get(key, 0)
        if old == new:
            continue
        moved = True
        delta = new - old
        verdict = ""
        if any(key.startswith(p) for p in _LOWER_IS_BETTER):
            verdict = "  BETTER" if delta < 0 else "  WORSE"
        print(f"   {key:44} {old:>8} -> {new:>8}  ({delta:+}){verdict}")
    if not moved:
        print("   no metric moved")
    print(f"\n{LIMITS}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", default=str(Path.home() / ".local/share/colophon/colophon.db"))
    parser.add_argument("--json", dest="out", help="write the metrics to this file as a baseline")
    parser.add_argument("--baseline", help="compare against a baseline written by --json")
    args = parser.parse_args()

    metrics = collect(args.db)
    if args.baseline:
        diff(json.loads(Path(args.baseline).read_text()), metrics)
    else:
        report(metrics)
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote baseline {args.out}")


if __name__ == "__main__":
    main()
