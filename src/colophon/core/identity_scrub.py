"""Scrub structural-marker values out of a book's identity fields.

The write-site gates stop a structural marker ("Chapter 04", "Part", a blank) from being written into
title/authors/series; this removes one already stored (e.g. from a scan predating the gates) so a
re-derive can replace it. Clears by VALUE (structural), the sibling of `_clear_weak_identity`, which
clears by PROVENANCE. An authoritative (manual / source-match) value is never scrubbed."""

from __future__ import annotations

from colophon.core.metadata_quality import is_structural_marker
from colophon.core.models import BookUnit, Provenance

# A user edit or an online-source match named the value; authoritative, never scrubbed.
_AUTHORITATIVE = frozenset({
    Provenance.MANUAL.value, Provenance.AUDNEXUS.value, Provenance.AUDIBLE.value,
    Provenance.HARDCOVER.value, Provenance.OPENLIBRARY.value, Provenance.GOOGLEBOOKS.value,
})


def scrub_structural_identity(book: BookUnit) -> None:
    """Clear any non-authoritative identity value that is a structural marker. In place. Idempotent."""
    prov = book.provenance
    if book.title and prov.get("title") not in _AUTHORITATIVE and is_structural_marker(book.title):
        book.title = None
        prov.pop("title", None)
    if book.authors and prov.get("authors") not in _AUTHORITATIVE:
        kept = [a for a in book.authors if not is_structural_marker(a)]
        if kept != book.authors:
            book.authors = kept
            if not kept:
                prov.pop("authors", None)
    if book.series and prov.get("series") not in _AUTHORITATIVE:
        kept = [s for s in book.series if not is_structural_marker(s.name)]
        if kept != book.series:
            book.series = kept
            if not kept:
                prov.pop("series", None)
