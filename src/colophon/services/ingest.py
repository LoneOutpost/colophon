"""Ingest service: scan a directory into persisted BookUnit candidates.

Scanning is non-destructive: an already-known folder keeps all of its app state
(cover, confidence, state, chapters, genres/tags, manual confirmation) and edited
fields; only empty fields are filled and the on-disk file list is refreshed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from colophon.adapters.audio import probe_audio_file
from colophon.adapters.repository.store import BookUnitRepo
from colophon.adapters.scan import group_book_units
from colophon.adapters.tags import read_embedded_tags
from colophon.core.classify import FileFeatures, classify
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import (
    BookUnit,
    Phase,
    PhaseState,
)
from colophon.core.phases import LOCAL, mark, resync_state, state_of
from colophon.services.identify import run_identify

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
    reconciled_folders: set[Path] = field(default_factory=set)


class ScanScope(StrEnum):
    NEW_ONLY = "new_only"   # add newly-discovered books; skip already-known ones
    UPDATE = "update"       # known books: re-run selected phases where stale/pending
    REFRESH = "refresh"     # known books: force selected phases even if fresh


@dataclass
class ScanOptions:
    scope: ScanScope = ScanScope.NEW_ONLY
    phases: frozenset[Phase] = field(default_factory=lambda: frozenset(LOCAL))
    book_ids: set[str] | None = None   # reserved for the selection-scoped follow-up


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
        logger.debug(f"scan {book.source_folder}: SEARCH probed {len(book.source_files)} files")

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
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"scan {book.source_folder}: CATEGORIZE content_kind={book.content_kind.value} "
                f"folder_kind={book.folder_kind.value} works={len(book.detected_works)} "
                f"signals={[(s.name, s.points) for s in book.classification_signals]}"
            )

    elif phase is Phase.IDENTIFY:
        run_identify(book, root=root, pattern=pattern, scheme=scheme)

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


def _scan_label(folder: Path, root: Path) -> str:
    """A readable per-folder progress label: the folder path relative to the scan root,
    or its bare name when it is not under root."""
    try:
        return str(folder.relative_to(root))
    except ValueError:
        return folder.name


def _plan_scan_all(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "",
                   progress: Callable[[int, int, str], None] | None = None) -> ScanPlan:
    """Compute what a scan of `root` would do, without writing anything."""
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    plan = ScanPlan()
    units = group_book_units(root)
    total = len(units)
    for i, unit in enumerate(units, start=1):
        if progress is not None:
            progress(i, total, _scan_label(unit.folder, root))
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


def plan_scan(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "",
              options: ScanOptions | None = None, inference_root: Path | None = None,
              progress: Callable[[int, int, str], None] | None = None) -> ScanPlan:
    """Compute what a scan of `root` would do, without writing anything.
    `options is None` keeps the legacy behavior (all books, all local phases).
    `inference_root` (default `root`) is the scan path used for classify/dir-inference depth.
    `progress(done, total, label)` fires once per folder as it is processed."""
    if options is None:
        return _plan_scan_all(repo, root, template=template,
                              directory_scheme=directory_scheme, progress=progress)
    if options.scope is ScanScope.NEW_ONLY:
        return _plan_scan_new_only(repo, root, options.phases, template=template,
                                   directory_scheme=directory_scheme,
                                   inference_root=inference_root, progress=progress)
    return _plan_scan_reprocess(repo, root, options.phases,
                                force=options.scope is ScanScope.REFRESH,
                                template=template, directory_scheme=directory_scheme,
                                inference_root=inference_root, progress=progress)


def _plan_scan_new_only(repo: BookUnitRepo, root: Path, phases: frozenset[Phase], *,
                        template: str, directory_scheme: str,
                        inference_root: Path | None = None,
                        progress: Callable[[int, int, str], None] | None = None) -> ScanPlan:
    """Ingest only books not already known; run the selected local phases on each.
    SEARCH is always run for a new book (probing is intrinsic to discovery)."""
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    inf_root = inference_root or root
    plan = ScanPlan()
    units = group_book_units(root)
    total = len(units)
    for i, unit in enumerate(units, start=1):
        if progress is not None:
            progress(i, total, _scan_label(unit.folder, root))
        if repo.get(BookUnit.id_for(unit.folder)) is not None:
            continue
        book = BookUnit.new(source_folder=unit.folder)
        run_local_phases(book, phases | {Phase.SEARCH}, force=False,
                         root=inf_root, pattern=pattern, scheme=scheme, unit_files=unit.files)
        plan.new_books += 1
        plan.files_added += len(book.source_files)
        plan.units.append(book)
    return plan


def _plan_scan_reprocess(repo: BookUnitRepo, root: Path, phases: frozenset[Phase], *,
                         force: bool, template: str, directory_scheme: str,
                         inference_root: Path | None = None,
                         progress: Callable[[int, int, str], None] | None = None) -> ScanPlan:
    """UPDATE (force=False) / REFRESH (force=True): add new books and re-process known ones
    in `root`. New books run the selected phases (always incl. SEARCH); known books re-run
    the selected phases — only STALE/PENDING unless `force`."""
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    inf_root = inference_root or root
    plan = ScanPlan()
    units = group_book_units(root)
    total = len(units)
    for i, unit in enumerate(units, start=1):
        if progress is not None:
            progress(i, total, _scan_label(unit.folder, root))
        existing = repo.get(BookUnit.id_for(unit.folder))
        book = existing if existing is not None else BookUnit.new(source_folder=unit.folder)
        run_phases = phases if existing is not None else (phases | {Phase.SEARCH})
        before_empty = _empty_fields(book) if existing is not None else set()
        prior_paths = {sf.path for sf in book.source_files}

        run_local_phases(book, run_phases, force=force, root=inf_root,
                         pattern=pattern, scheme=scheme, unit_files=unit.files)

        plan.files_added += len({sf.path for sf in book.source_files} - prior_paths)
        if existing is not None:
            plan.existing_books += 1
            plan.fields_filled += len(before_empty - _empty_fields(book))
        else:
            plan.new_books += 1
        plan.units.append(book)
    return plan


def plan_rescan_books(
    repo: BookUnitRepo,
    books: list[BookUnit],
    phases: frozenset[Phase],
    *,
    force: bool,
    template: str,
    directory_scheme: str,
    root_for: Callable[[BookUnit], Path],
    progress: Callable[[int, int, str], None] | None = None,
) -> ScanPlan:
    """Re-process exactly `books` (selection-scoped).

    `root_for(book) -> Path` gives the configured scan root for inference.
    Known books only; never discovers new ones. Files for a forced/stale
    SEARCH are re-probed from a cheap per-folder walk.
    """
    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    plan = ScanPlan()
    total = len(books)
    for i, book in enumerate(books, start=1):
        if progress is not None:
            progress(i, total, book.title or book.source_folder.name)
        units = group_book_units(book.source_folder)
        unit_files = next((u.files for u in units if u.folder == book.source_folder), [])
        before_empty = _empty_fields(book)
        prior_paths = {sf.path for sf in book.source_files}
        run_local_phases(
            book, phases, force=force, root=root_for(book),
            pattern=pattern, scheme=scheme, unit_files=unit_files,
        )
        plan.files_added += len({sf.path for sf in book.source_files} - prior_paths)
        plan.existing_books += 1
        plan.fields_filled += len(before_empty - _empty_fields(book))
        plan.units.append(book)
    return plan


def _adopt_and_identify(
    unit: BookUnit, repo: BookUnitRepo, *, root: Path, pattern: object, scheme: object,
) -> BookUnit:
    """Return the unit to persist for one projected BookUnit.

    A SINGLE/UNKNOWN container arrives already identified (plan_scan ran its phases and
    merged existing state) — pass it through. A fresh leaf (IDENTIFY not FRESH) is
    merged onto its persisted counterpart (by leaf id) so prior app state survives,
    then identified on its own file subset. A leaf persists even if IDENTIFY fails."""
    if state_of(unit, Phase.IDENTIFY) is PhaseState.FRESH:
        return unit  # already-identified container (the SINGLE path)

    existing = repo.get(unit.id)
    if existing is not None:
        # Existing leaf is the base: keep cover/confidence/state/manual edits and any
        # filled identity. Refresh only the structural fields the new split provides,
        # and fill the seeded identity where the existing row left it empty.
        existing.source_files = unit.source_files
        existing.content_kind = unit.content_kind
        existing.detected_works = unit.detected_works
        if not existing.title and unit.title:
            existing.title = unit.title
            existing.provenance["title"] = unit.provenance.get("title", "")
        if not existing.authors and unit.authors:
            existing.authors = list(unit.authors)
            existing.provenance["authors"] = unit.provenance.get("authors", "")
        if not existing.series and unit.series:
            existing.series = list(unit.series)
            existing.provenance["series"] = unit.provenance.get("series", "")
        unit = existing

    mark(unit, Phase.SEARCH, PhaseState.FRESH)      # files were probed at container level
    mark(unit, Phase.CATEGORIZE, PhaseState.FRESH)  # being SINGLE is its categorization
    try:
        run_identify(unit, root=root, pattern=pattern, scheme=scheme)
        mark(unit, Phase.IDENTIFY, PhaseState.FRESH)
    except Exception as e:  # a leaf must persist even if IDENTIFY fails (minimal identity)
        logger.warning(f"leaf IDENTIFY failed for {unit.source_folder} [{unit.title!r}]: {e}")
        mark(unit, Phase.IDENTIFY, PhaseState.FAILED, detail=str(e))
    resync_state(unit)
    unit.touch()
    return unit


def plan_scan_graph(
    repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "",
    options: ScanOptions | None = None, inference_root: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ScanPlan:
    """Graph-routed planner: persist `project(build_graph(...))` — single containers and
    multi-book leaves — with per-leaf IDENTIFY and state preservation. `reconciled_folders`
    are the folders it fully recomputed, so commit can prune what their unit set replaced."""
    # Lazy import: graph_build imports plan_scan from this module, so a module-scope
    # import of build_graph would create an import cycle.
    from colophon.core.graph_resolve import resolve_graph_authors
    from colophon.services.graph_build import build_graph, project

    pattern = compile_template(template)
    scheme = parse_scheme(directory_scheme)
    inf_root = inference_root or root
    graph = build_graph(
        repo, root, template=template, directory_scheme=directory_scheme,
        options=options, inference_root=inference_root, progress=progress,
    )
    plan = ScanPlan()
    for unit in project(graph):
        existing = repo.get(unit.id)
        before_empty = _empty_fields(existing) if existing is not None else set()
        prior_paths = {sf.path for sf in existing.source_files} if existing is not None else set()
        adopted = _adopt_and_identify(unit, repo, root=inf_root, pattern=pattern, scheme=scheme)
        if existing is not None:
            plan.existing_books += 1
            plan.fields_filled += len(before_empty - _empty_fields(adopted))
        else:
            plan.new_books += 1
        plan.files_added += len({sf.path for sf in adopted.source_files} - prior_paths)
        plan.units.append(adopted)
        plan.reconciled_folders.add(adopted.source_folder)
    resolve_graph_authors(graph, plan.units, root=root)
    return plan


def commit_scan(repo: BookUnitRepo, plan: ScanPlan, *, reconcile: bool = False) -> int:
    """Persist a computed plan; returns the number of books written.

    With `reconcile`, for each folder the plan fully recomputed (`reconciled_folders`)
    every persisted book in that folder whose id is not in the plan's new unit set is
    deleted first — pruning a stale container that flipped to leaves, or a leaf the
    re-cluster no longer produces. A pruned id is never one we re-upsert."""
    if reconcile:
        keep_by_folder: dict[Path, set[str]] = {}
        for book in plan.units:
            keep_by_folder.setdefault(book.source_folder, set()).add(book.id)
        for folder in plan.reconciled_folders:
            keep = keep_by_folder.get(folder, set())
            for stale_id in repo.ids_in_folder(folder) - keep:
                repo.delete(stale_id)
    for book in plan.units:
        repo.upsert(book)
    return len(plan.units)


def scan_ingest(repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "") -> list[BookUnit]:
    """Plan and commit a scan of `root` in one call; returns the persisted units.
    Routes through the entity graph: multi-book folders persist as leaves, the stale
    container is pruned, existing leaf state is preserved."""
    plan = plan_scan_graph(repo, root, template=template, directory_scheme=directory_scheme)
    commit_scan(repo, plan, reconcile=True)
    return plan.units
