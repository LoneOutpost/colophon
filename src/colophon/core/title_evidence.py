"""Title field resolution from a weighted-evidence ballot, run AFTER the author ballot. Candidates:
the book-level detected-work label, the committed tag/datafile title, and the title TOKENS isolated
from the folder name and filename by `title_candidates`. A candidate that is blank, junk-shaped, or
EQUALS a resolved author is penalized to zero (an author is never a title) — this is what un-smuggles
the ~476 author-as-title books. A cohort-constant candidate earns a bonus; a manual/match title
settles. See tasks/2026-08-12-title-after-author-design.md."""

from __future__ import annotations

from colophon.core import evidence_weights as W
from colophon.core.cohort_constancy import cohort_constant_tokens
from colophon.core.field_resolve import FieldEvidence, ResolvedField, resolve_field
from colophon.core.identity_tokens import title_candidates
from colophon.core.metadata_quality import is_junk_title
from colophon.core.models import AUTHORITATIVE_PROV, BookUnit, Provenance
from colophon.core.normalize import normalize_key, normalize_text

_SETTLE_PROV = AUTHORITATIVE_PROV
_RECOVER = {Provenance.TAG.value: (W.W_T_TAG, "tag"), Provenance.DATAFILE.value: (W.W_T_DATAFILE, "datafile")}
_PROV_FOR = {"tag": Provenance.TAG.value, "datafile": Provenance.DATAFILE.value,
             "folder": Provenance.DIRECTORY.value, "filename": Provenance.FILENAME.value}


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _penalized(value: str, weight: float, author_keys: set[str]) -> float:
    """Junk, blank, or a value equal to a resolved author -> 0 (kept for the readout, out of tally)."""
    if not value or not value.strip() or is_junk_title(value) or normalize_key(value) in author_keys:
        return 0.0
    return weight


def collect_title_evidence(book: BookUnit) -> list[FieldEvidence]:
    ev: list[FieldEvidence] = []
    authors = list(book.authors)
    series = [s.name for s in book.series]
    akeys = {normalize_key(a) for a in authors}

    # NB: the detected-work label is deliberately NOT a candidate — the clusterer's parse is often a
    # truncated/dirty version of the filename ('weird' from 'weird_name_no_delimiters'). The tokenized
    # filename candidate below is the clean source.
    prov = book.provenance.get("title", "")
    if book.title and prov in _RECOVER:
        w, src = _RECOVER[prov]
        ev.append(FieldEvidence(book.title, _penalized(book.title, w, akeys), src,
                                f"{src} title '{book.title}'"))

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
    """Resolve the title after the author is known and stamp the winner (normalized) onto `book`. A
    manual/match title settles; an empty ballot leaves the title as-is.

    Conservative by design: only intervene when the current title is a smuggled author or junk. A real
    title is kept verbatim, so a good already-derived title is never degraded by a noisier folder or
    filename token — the ballot targets the smuggling, not every book."""
    if book.title and book.provenance.get("title") in _SETTLE_PROV:
        return ResolvedField(book.title, 1.0, [])
    if book.title:
        akeys = {normalize_key(a) for a in book.authors}
        if normalize_key(book.title) not in akeys and not is_junk_title(book.title):
            return ResolvedField(book.title, 1.0, [])
    r = resolve_field(collect_title_evidence(book))
    if r.value is None:
        return r
    book.title = normalize_text(r.value)
    book.provenance["title"] = _PROV_FOR.get(r.source, book.provenance.get("title") or Provenance.FILENAME.value)
    return r
