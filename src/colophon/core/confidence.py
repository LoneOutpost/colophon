"""Transparent weighted confidence scoring for book identification.

Pure function over (candidate, source results). Signals are additive, clamped to
[0, 100], and retained so the UI can explain every score.
"""

from __future__ import annotations

from colophon.core.isbn import isbn_equal
from colophon.core.match import title_author_score
from colophon.core.models import BookUnit, ConfidenceSignal, _Base
from colophon.core.sources import SourceResult

_AGREEMENT_THRESHOLD = 0.85  # title/author blend above this "agrees" with candidate
_AUTHORITY_MARGIN = 0.05  # fuzzy-score window within which authority decides "best"


class IdentificationOutcome(_Base):
    confidence: float
    signals: list[ConfidenceSignal] = []  # noqa: RUF012 - pydantic field default, copied per instance
    ranked: list[SourceResult] = []  # noqa: RUF012 - pydantic field default, copied per instance
    best: SourceResult | None = None


def _result_score(book: BookUnit, result: SourceResult) -> float:
    return title_author_score(book.title, book.authors, result.title, result.authors)


def score_identification(
    book: BookUnit, results: list[SourceResult], *, authority: dict[str, int] | None = None
) -> IdentificationOutcome:
    signals: list[ConfidenceSignal] = []
    score = 0.0

    # Embedded-tag completeness: a clean candidate is itself weak evidence,
    # awarded regardless of whether external sources returned anything.
    if book.title and book.authors:
        score += 15
        signals.append(ConfidenceSignal(name="embedded_core", points=15, detail="title+author present"))

    if not results:
        return IdentificationOutcome(confidence=max(0.0, min(100.0, score)), signals=signals)

    # title/author closeness is a fuzzy match used several times below; compute
    # it once per result rather than re-running SequenceMatcher on every read.
    score_for = {id(r): _result_score(book, r) for r in results}

    def _rank_key(r: SourceResult) -> tuple[float, float]:
        closeness = 0.0
        if book.duration_ms > 0 and r.runtime_ms:
            closeness = -abs(r.runtime_ms - book.duration_ms)
        return (score_for[id(r)], closeness)

    ranked = sorted(results, key=_rank_key, reverse=True)
    best = ranked[0]

    if authority:
        top = score_for[id(best)]
        comparable = [
            r for r in results
            if score_for[id(r)] >= _AGREEMENT_THRESHOLD and score_for[id(r)] >= top - _AUTHORITY_MARGIN
        ]
        if comparable:
            best = min(
                comparable,
                key=lambda r: (authority.get(r.provider, len(authority)), -score_for[id(r)]),
            )

    # ASIN exact match — strongest single signal.
    asin_hit = book.asin and any(r.asin and r.asin == book.asin for r in results)
    if asin_hit:
        score += 60
        signals.append(ConfidenceSignal(name="asin_exact_match", points=60, detail=f"ASIN {book.asin}"))

    # ISBN exact match — equally strong; ISBN-10 and its ISBN-13 are treated as equal.
    isbn_hit = book.isbn and any(isbn_equal(book.isbn, r.isbn) for r in results)
    if isbn_hit:
        score += 60
        signals.append(ConfidenceSignal(name="isbn_exact_match", points=60, detail=f"ISBN {book.isbn}"))

    # Cross-source agreement on title+author — counted by DISTINCT provider, with
    # points scaled by match quality so a near-perfect agreement scores higher than
    # a borderline one. Tuned so a strong two-source agreement (15 embedded + ~60)
    # reaches the default review threshold without needing an ASIN match.
    agreeing_providers = {
        r.provider for r in results if score_for[id(r)] >= _AGREEMENT_THRESHOLD
    }
    if agreeing_providers:
        quality = max(score_for[id(r)] for r in results if r.provider in agreeing_providers)
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
    best_score = score_for[id(best)]
    if best_score < 0.5:
        penalty = -25
        score += penalty
        signals.append(ConfidenceSignal(name="disagreement_penalty", points=penalty, detail=f"best match {best_score:.2f}"))

    if book.duration_ms > 0 and best.runtime_ms:
        rel = abs(best.runtime_ms - book.duration_ms) / book.duration_ms
        if rel <= 0.05:
            score += 12
            signals.append(ConfidenceSignal(name="runtime_match", points=12, detail=f"runtime within {rel * 100:.0f}%"))
        elif rel >= 0.25:
            score += -15
            signals.append(ConfidenceSignal(name="runtime_mismatch", points=-15, detail=f"runtime off by {rel * 100:.0f}% (abridged or wrong edition?)"))

    if book.abridged is not None and best.abridged is not None:
        if book.abridged == best.abridged:
            score += 5
            signals.append(ConfidenceSignal(name="format_match", points=5, detail="abridged flag agrees"))
        else:
            score += -15
            signals.append(ConfidenceSignal(name="format_mismatch", points=-15, detail="abridged flag differs"))

    if authority and score_for[id(best)] >= _AGREEMENT_THRESHOLD:
        rank = authority.get(best.provider, len(authority))
        maxrank = max(authority.values())
        bonus = round(10 * (1 - rank / maxrank)) if maxrank > 0 else 10
        if bonus > 0:
            score += bonus
            signals.append(ConfidenceSignal(
                name="source_authority", points=bonus,
                detail=f"{best.provider} (authority #{rank + 1})",
            ))

    confidence = max(0.0, min(100.0, score))
    return IdentificationOutcome(confidence=confidence, signals=signals, ranked=ranked, best=best)
