"""Transparent weighted confidence scoring for book identification.

Pure function over (candidate, source results). Signals are additive, clamped to
[0, 100], and retained so the UI can explain every score.
"""

from __future__ import annotations

from colophon.core.match import title_author_score
from colophon.core.models import BookUnit, ConfidenceSignal, _Base
from colophon.core.sources import SourceResult

_AGREEMENT_THRESHOLD = 0.85  # title/author blend above this "agrees" with candidate


class IdentificationOutcome(_Base):
    confidence: float
    signals: list[ConfidenceSignal] = []  # noqa: RUF012 - pydantic field default, copied per instance
    ranked: list[SourceResult] = []  # noqa: RUF012 - pydantic field default, copied per instance
    best: SourceResult | None = None


def _result_score(book: BookUnit, result: SourceResult) -> float:
    return title_author_score(book.title, book.authors, result.title, result.authors)


def score_identification(book: BookUnit, results: list[SourceResult]) -> IdentificationOutcome:
    signals: list[ConfidenceSignal] = []
    score = 0.0

    # Embedded-tag completeness: a clean candidate is itself weak evidence,
    # awarded regardless of whether external sources returned anything.
    if book.title and book.authors:
        score += 15
        signals.append(ConfidenceSignal(name="embedded_core", points=15, detail="title+author present"))

    if not results:
        return IdentificationOutcome(confidence=max(0.0, min(100.0, score)), signals=signals)

    ranked = sorted(results, key=lambda r: _result_score(book, r), reverse=True)
    best = ranked[0]

    # ASIN exact match — strongest single signal.
    asin_hit = book.asin and any(r.asin and r.asin == book.asin for r in results)
    if asin_hit:
        score += 60
        signals.append(ConfidenceSignal(name="asin_exact_match", points=60, detail=f"ASIN {book.asin}"))

    # Cross-source agreement on title+author — counted by DISTINCT provider, with
    # points scaled by match quality so a near-perfect agreement scores higher than
    # a borderline one. Tuned so a strong two-source agreement (15 embedded + ~60)
    # reaches the default review threshold without needing an ASIN match.
    agreeing_providers = {
        r.provider for r in results if _result_score(book, r) >= _AGREEMENT_THRESHOLD
    }
    if agreeing_providers:
        quality = max(_result_score(book, r) for r in results if r.provider in agreeing_providers)
        if len(agreeing_providers) >= 2:
            pts = round(60 * quality)
            score += pts
            signals.append(ConfidenceSignal(
                name="cross_source_agreement", points=pts,
                detail=f"{len(agreeing_providers)} providers agree ({quality:.2f})",
            ))
        else:
            pts = round(30 * quality)
            score += pts
            signals.append(ConfidenceSignal(
                name="single_source_match", points=pts, detail=f"1 provider agrees ({quality:.2f})",
            ))

    # Disagreement penalty — best match is poor.
    best_score = _result_score(book, best)
    if best_score < 0.5:
        penalty = -25
        score += penalty
        signals.append(ConfidenceSignal(name="disagreement_penalty", points=penalty, detail=f"best match {best_score:.2f}"))

    confidence = max(0.0, min(100.0, score))
    return IdentificationOutcome(confidence=confidence, signals=signals, ranked=ranked, best=best)
