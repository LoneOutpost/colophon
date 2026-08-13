"""Title field resolution from a weighted-evidence ballot, run AFTER the author ballot. A pure scorer:
every source casts a weighted `FieldEvidence` and the heaviest value wins — no gate around the
election. Candidates: the detected-work label, the committed title (recovered at a dominant weight so a
decided title wins unless the penalty zeros it), and the title TOKENS isolated
from the folder name and filename by `title_candidates`. A candidate that is blank, junk-shaped, or
EQUALS a resolved author is penalized to zero (an author is never a title) — this un-smuggles the
author-as-title books while a good committed title simply out-weighs the raw tokens. A cohort-constant
candidate earns a bonus; a manual/match title settles. See tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

from colophon.core import evidence_weights as W
from colophon.core.cohort_constancy import cohort_constant_tokens
from colophon.core.field_resolve import FieldEvidence, ResolvedField, resolve_field
from colophon.core.identity_tokens import title_candidates
from colophon.core.metadata_quality import title_junk
from colophon.core.models import AUTHORITATIVE_PROV, BookUnit, Provenance
from colophon.core.normalize import normalize_key, normalize_text
from colophon.core.sequence_affix import clean_title

_SETTLE_PROV = AUTHORITATIVE_PROV
_RECOVER = {
    Provenance.TAG.value: (W.W_T_TAG, "tag"),
    Provenance.DATAFILE.value: (W.W_T_DATAFILE, "datafile"),
    Provenance.DIRECTORY.value: (W.W_T_FOLDER, "folder"),
    Provenance.FILENAME.value: (W.W_T_FILENAME, "filename"),
}
_PROV_FOR = {"tag": Provenance.TAG.value, "datafile": Provenance.DATAFILE.value,
             "folder": Provenance.DIRECTORY.value, "filename": Provenance.FILENAME.value}


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _penalized(value: str, weight: float, author_keys: set[str]) -> float:
    """Two forces, kept distinct. Winner-exclusion — a value the resolved author already claimed —
    HARD-zeros (relational). Otherwise the intrinsic title-junk magnitude scales the weight down
    (scalar, not a veto): a `.-.`-spanning folder string, a bare `(5)`, an embedded `N of M`, a
    leading index all shed most of their weight so a clean token out-votes them."""
    if not value or not value.strip() or normalize_key(value) in author_keys:
        return 0.0
    return weight * (1.0 - title_junk(value))


def collect_title_evidence(book: BookUnit) -> list[FieldEvidence]:
    ev: list[FieldEvidence] = []
    authors = list(book.authors)
    series = [s.name for s in book.series]
    akeys = {normalize_key(a) for a in authors}

    dw = book.detected_works[0] if book.detected_works else None
    if dw and dw.label:
        src, w = ("tag", W.W_T_TAG) if dw.label_prov == Provenance.TAG.value else ("filename", W.W_T_FILENAME)
        ev.append(FieldEvidence(dw.label, _penalized(dw.label, w, akeys), src, f"{src} label '{dw.label}'"))

    # The committed title (already decided by reconcile) votes at a DOMINANT weight: it wins the
    # election unless the junk/author penalty zeros it, at which point a token wins instead. This is
    # the canonical, in-election form of "only overturn a title on clear evidence it is wrong" — a
    # scorer, not a gate around the ballot.
    prov = book.provenance.get("title", "")
    if book.title and prov in _RECOVER:
        _, src = _RECOVER[prov]
        ev.append(FieldEvidence(book.title, _penalized(book.title, W.W_T_COMMITTED, akeys), src,
                                f"committed {src} title '{book.title}'"))

    for tok in title_candidates(book.source_folder.name, authors=authors, series=series):
        ev.append(FieldEvidence(tok, _penalized(tok, W.W_T_FOLDER, akeys), "folder", f"folder title '{tok}'"))
    if book.source_files:
        stem = book.source_files[0].path.stem
        for tok in title_candidates(stem, authors=authors, series=series):
            ev.append(FieldEvidence(tok, _penalized(tok, W.W_T_FILENAME, akeys), "filename",
                                    f"filename title '{tok}'"))

    if book.source_files:
        const = {_norm(t) for t in cohort_constant_tokens([sf.path.stem for sf in book.source_files])}
        for cand in list(ev):
            if cand.weight > 0 and _norm(cand.value) in const:
                ev.append(FieldEvidence(cand.value, W.W_T_CONSTANCY_BONUS, "constancy",
                                        f"'{cand.value}' constant across the cohort"))
    return ev


def resolve_title(book: BookUnit) -> ResolvedField:
    """Resolve the title after the author is known and stamp the winner onto `book`. A manual/match
    title settles; an empty ballot, or one the committed title re-wins, leaves the title verbatim (it
    is already normalized). Only an overturned title is re-normalized."""
    if book.title and book.provenance.get("title") in _SETTLE_PROV:
        return ResolvedField(book.title, 1.0, [])
    r = resolve_field(collect_title_evidence(book))
    if r.value is None:
        return r
    if r.value == book.title:
        # The committed title re-wins: clean cruft off it in place (an encoding tail / part index a
        # folder token carries never passed through repair_fields, which runs BEFORE this graph pass),
        # but do NOT re-normalize its case — it is already the committed value.
        cleaned = clean_title(book.title)
        if cleaned and cleaned != book.title:
            book.title = cleaned
        return r
    # An overturn: normalize + clean the new winner.
    cleaned = clean_title(normalize_text(r.value))
    if not cleaned or cleaned == book.title:
        return r
    book.title = cleaned
    book.provenance["title"] = _PROV_FOR.get(r.source, book.provenance.get("title") or Provenance.FILENAME.value)
    return r
