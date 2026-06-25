"""Ingest service: scan a directory into persisted BookUnit candidates.

Scanning is non-destructive: an already-known folder keeps all of its app state
(cover, confidence, state, chapters, genres/tags, manual confirmation) and edited
fields; only empty fields are filled and the on-disk file list is refreshed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from colophon.adapters.audio import probe_audio_file
from colophon.adapters.repository.store import BookUnitRepo
from colophon.adapters.scan import group_book_units
from colophon.adapters.sidecar import read_sidecar
from colophon.adapters.tags import read_embedded_tags
from colophon.core.classify import FileFeatures, classify
from colophon.core.dirinfer import infer_from_path, parse_scheme
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.models import BookUnit
from colophon.core.reconcile import reconcile

logger = logging.getLogger(__name__)

_RECONCILED_FIELDS = (
    "title", "subtitle", "authors", "narrators", "series",
    "publish_year", "publisher", "description", "asin", "isbn",
)


@dataclass
class ScanPlan:
    units: list[BookUnit] = field(default_factory=list)
    new_books: int = 0
    existing_books: int = 0
    fields_filled: int = 0
    files_added: int = 0


def _empty_fields(book: BookUnit) -> set[str]:
    out: set[str] = set()
    for name in _RECONCILED_FIELDS:
        value = getattr(book, name)
        if value is None or value == "" or value == []:
            out.add(name)
    return out


def plan_scan(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "") -> ScanPlan:
    """Compute what a scan of `root` would do, without writing anything."""
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    plan = ScanPlan()
    for unit in group_book_units(root):
        existing = repo.get(BookUnit.id_for(unit.folder))
        book = existing if existing is not None else BookUnit.new(source_folder=unit.folder)

        prior_paths = {sf.path for sf in book.source_files}
        book.source_files = [probe_audio_file(p) for p in unit.files]
        plan.files_added += len({sf.path for sf in book.source_files} - prior_paths)

        first = unit.files[0]
        embedded = read_embedded_tags(first)

        # Build per-file features for the structural classifier. Reuse the
        # first-file tag read; only fan out the remaining reads when needed
        # (the cost gate — a 1-file folder is trivially single).
        features = []
        for sf in book.source_files:
            tags = embedded if sf.path == first else read_embedded_tags(sf.path)
            features.append(
                FileFeatures(path=sf.path, ext=sf.ext,
                             duration_seconds=sf.duration_seconds, tags=tags)
            )
        try:
            result = classify(unit.folder, root, features,
                              template_pattern=pattern, scheme_patterns=scheme)
            book.content_kind = result.content_kind
            book.folder_kind = result.folder_kind
            book.classification_confidence = result.confidence
            book.classification_signals = result.signals
            book.findings = result.findings
            book.detected_works = result.detected_works
        except Exception as e:  # classification must never fail a scan
            logger.warning(f"classification failed for {unit.folder}: {e}")

        filename_fields = parse_filename(pattern, first.name) or {}
        sidecar = read_sidecar(unit.folder)
        directory_fields = infer_from_path(unit.folder, root, scheme)

        before_empty = _empty_fields(book) if existing is not None else set()
        reconcile(
            book,
            embedded=embedded,
            sidecar=sidecar,
            dir_title=unit.folder.name,
            filename_fields=filename_fields,
            directory_fields=directory_fields,
        )
        if existing is not None:
            plan.existing_books += 1
            plan.fields_filled += len(before_empty - _empty_fields(book))
        else:
            plan.new_books += 1

        plan.units.append(book)
    return plan


def commit_scan(repo: BookUnitRepo, plan: ScanPlan) -> int:
    """Persist a computed plan; returns the number of books written."""
    for book in plan.units:
        repo.upsert(book)
    return len(plan.units)


def scan_ingest(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "") -> list[BookUnit]:
    """Plan and commit a scan of `root` in one call; returns the persisted units."""
    plan = plan_scan(repo, root, template=template, directory_scheme=directory_scheme)
    commit_scan(repo, plan)
    return plan.units
