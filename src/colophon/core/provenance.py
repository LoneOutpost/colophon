"""Human-facing labels and tooltips for the local provenance tiers (where a scanned
field value came from). Match-source provenance (audnexus/…) is labelled elsewhere via
the live source list; these helpers return None for anything they don't own."""

from __future__ import annotations

from colophon.core.models import Provenance

_LABELS: dict[str, str] = {
    Provenance.TAG.value: "File tag",
    Provenance.DATAFILE.value: "Datafile",
    Provenance.DIRECTORY.value: "Folder",
    Provenance.FILENAME.value: "Filename",
    Provenance.GRAPHING.value: "Inferred",
    Provenance.MANUAL.value: "Edited",
}

_TOOLTIPS: dict[str, str] = {
    Provenance.TAG.value: "Read from the file's embedded tags.",
    Provenance.DATAFILE.value: "Read from a metadata.json datafile sidecar in the folder.",
    Provenance.DIRECTORY.value: "Inferred from the folder layout (directory scheme).",
    Provenance.FILENAME.value: "Parsed from the file name.",
    Provenance.GRAPHING.value: (
        "Inferred from the author folder (a nearby tagged book named the author)."
    ),
    Provenance.MANUAL.value: "You set this value.",
}


def provenance_label(value: str | None) -> str | None:
    """Human label for a local provenance tier, or None if not a local tier."""
    return _LABELS.get(value) if value else None


def provenance_tooltip(value: str | None) -> str | None:
    """One-line explanation of a local provenance tier, or None if not a local tier."""
    return _TOOLTIPS.get(value) if value else None
