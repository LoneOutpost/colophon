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
from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import LOCAL, mark, resync_state, state_of
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


def _run_local(
    book: BookUnit,
    phase: Phase,
    *,
    root: Path,
    pattern: object,
    scheme: object,
    unit_files: list[Path] | None = None,
) -> None:
    """Execute one local phase's work for `book`.

    SEARCH: re-probe source_files from on-disk audio (requires unit_files).
    CATEGORIZE: build FileFeatures and run classify; sets classification fields.
    IDENTIFY: read embedded/sidecar/filename/directory fields; calls reconcile.

    Raises on failure — callers decide how to mark the phase (FRESH or FAILED).
    `unit_files` is required for SEARCH; ignored for CATEGORIZE and IDENTIFY.
    """
    if phase is Phase.SEARCH:
        if unit_files is None:
            raise ValueError("unit_files required for SEARCH phase")
        book.source_files = [probe_audio_file(p) for p in unit_files]

    elif phase is Phase.CATEGORIZE:
        first_path = book.source_files[0].path if book.source_files else None
        embedded = read_embedded_tags(first_path) if first_path else None
        features = []
        for sf in book.source_files:
            tags = embedded if sf.path == first_path else read_embedded_tags(sf.path)
            features.append(
                FileFeatures(path=sf.path, ext=sf.ext,
                             duration_seconds=sf.duration_seconds, tags=tags)
            )
        result = classify(book.source_folder, root, features,
                          template_pattern=pattern, scheme_patterns=scheme)
        book.content_kind = result.content_kind
        book.folder_kind = result.folder_kind
        book.classification_confidence = result.confidence
        book.classification_signals = result.signals
        book.findings = result.findings
        book.detected_works = result.detected_works

    elif phase is Phase.IDENTIFY:
        first_path = book.source_files[0].path if book.source_files else None
        embedded = read_embedded_tags(first_path) if first_path else None
        filename_fields = parse_filename(pattern, first_path.name) if first_path else {}
        sidecar = read_sidecar(book.source_folder)
        directory_fields = infer_from_path(book.source_folder, root, scheme)
        reconcile(
            book,
            embedded=embedded,
            sidecar=sidecar,
            dir_title=book.source_folder.name,
            filename_fields=filename_fields or {},
            directory_fields=directory_fields,
        )

    else:
        raise ValueError(f"_run_local: unsupported phase {phase!r}")


def run_local_phases(
    book: BookUnit, phases: frozenset[Phase], *, force: bool,
    root: Path, pattern: object, scheme: object,
    unit_files: list[Path] | None = None,
) -> None:
    """Run the requested LOCAL phases for `book`, in pipeline order. A phase runs when
    `force` or its state is STALE/PENDING (so non-force mirrors the old refresh_local).
    FRESH on success; FAILED stops the chain. Always resyncs the derived state."""
    def _should(phase: Phase) -> bool:
        return phase in phases and (force or state_of(book, phase) in (PhaseState.STALE, PhaseState.PENDING))

    if unit_files is None and _should(Phase.SEARCH):
        units = group_book_units(root)
        match = next((u for u in units if u.folder == book.source_folder), None)
        unit_files = match.files if match else []

    for phase in (Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY):
        if not _should(phase):
            continue
        try:
            _run_local(book, phase, root=root, pattern=pattern, scheme=scheme, unit_files=unit_files)
            mark(book, phase, PhaseState.FRESH)
        except Exception as e:  # a local phase must not crash the caller
            logger.warning(f"local phase {phase} failed for {book.source_folder}: {e}")
            mark(book, phase, PhaseState.FAILED, detail=str(e))
            break
    resync_state(book)


def refresh_local(book: BookUnit, *, root: Path, template: str, directory_scheme: str) -> None:
    """Re-run the STALE/PENDING local phases for one already-known book, in order.
    FRESH on success; FAILED stops the chain. Mirrors plan_scan's per-book body."""
    run_local_phases(
        book, frozenset(LOCAL), force=False,
        root=root, pattern=compile_template(template), scheme=parse_scheme(directory_scheme),
    )


def plan_scan(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "") -> ScanPlan:
    """Compute what a scan of `root` would do, without writing anything."""
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    plan = ScanPlan()
    for unit in group_book_units(root):
        existing = repo.get(BookUnit.id_for(unit.folder))
        book = existing if existing is not None else BookUnit.new(source_folder=unit.folder)

        # SEARCH phase — capture prior paths before probing for files_added accounting
        prior_paths = {sf.path for sf in book.source_files}
        _run_local(book, Phase.SEARCH, root=root, pattern=pattern, scheme=scheme,
                   unit_files=unit.files)
        mark(book, Phase.SEARCH, PhaseState.FRESH)
        plan.files_added += len({sf.path for sf in book.source_files} - prior_paths)

        # CATEGORIZE phase
        try:
            _run_local(book, Phase.CATEGORIZE, root=root, pattern=pattern, scheme=scheme)
            mark(book, Phase.CATEGORIZE, PhaseState.FRESH)
        except Exception as e:  # classification must never fail a scan
            logger.warning(f"classification failed for {unit.folder}: {e}")
            mark(book, Phase.CATEGORIZE, PhaseState.FAILED, detail=str(e))

        # IDENTIFY phase
        before_empty = _empty_fields(book) if existing is not None else set()
        _run_local(book, Phase.IDENTIFY, root=root, pattern=pattern, scheme=scheme)
        mark(book, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(book)

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
