"""UI-agnostic orchestration of the Colophon pipeline. The UI calls only this."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from colophon.adapters.audio import is_audio_file
from colophon.adapters.config import PATTERN_HISTORY_CAP, Config, OrganizePattern, save_config
from colophon.adapters.cover import mime_for_suffix
from colophon.adapters.downloader import (
    DownloadCancelled,  # noqa: F401 - re-exported for the Acquire UI
)
from colophon.adapters.lazylibrarian import PathPatterns, read_audiobook_patterns
from colophon.adapters.realdebrid import RdUser, RealDebridClient, RealDebridSource
from colophon.adapters.realdebrid_cache import CachingRealDebridSource
from colophon.adapters.tags import read_embedded_tags
from colophon.app_context import AppContext, build_all_sources, default_db_path
from colophon.core.cancel import CancelToken
from colophon.core.catalog import CatalogEntry, list_entries
from colophon.core.chapters import Chapter, normalize_chapters, runtime_mismatch
from colophon.core.confidence import (
    IdentificationOutcome,
    score_identification,
    sort_by_runtime_closeness,
)
from colophon.core.duplicate_check import (
    CollidingBook,
    DuplicateDestination,
    duplicate_targets,
)
from colophon.core.entity_alias import canonical_book
from colophon.core.entity_graph import entity_graph_from_records
from colophon.core.fields import get_field
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.genre_policy import GenrePolicy
from colophon.core.graph import DirectoryNode, Graph
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import (
    ancestor_franchise,
    apply_franchise_fill,
    book_node_id,
    book_records,
    graph_from_records,
    prune_dangling_edges,
    resolve_book_franchise,
    skeleton_records,
)
from colophon.core.graph_resolve import (
    _name_key,
    apply_confirmed_overrides,
    franchise_for,
)
from colophon.core.graph_view import grouping_cohort
from colophon.core.jobs import Job
from colophon.core.library_graph import reconcile
from colophon.core.models import (
    SUPPRESSED_FINDINGS,
    BookState,
    BookUnit,
    ConfidenceSignal,
    EditChange,
    EmbeddedTags,
    Finding,
    FindingCode,
    FindingSeverity,
    OperationRecord,
    Phase,
    PhaseState,
    Provenance,
    _Base,
    new_batch_id,
)
from colophon.core.navigator import (
    DirectoryListing,
    DirEntry,
    LibraryTree,
    build_library_tree,
    filter_library_tree,
)
from colophon.core.node_classify import book_identity_confidence, classify_nodes
from colophon.core.normalize import FIELD_NORMALIZERS, merge_preserve, normalize_genres
from colophon.core.part_order import resolve_part_order
from colophon.core.pathscheme import build_reorg_targets, build_target_path
from colophon.core.perf import timed
from colophon.core.phases import (
    LOCAL,
    ensure_phases,
    invalidate_from,
    mark,
    phases_from,
    resync_state,
    state_of,
)
from colophon.core.provenance import provenance_label, provenance_tooltip
from colophon.core.quickmatch import (
    IdentifyPlan,
    IdentifySummary,
    QuickMatchProposal,
    QuickMatchSummary,
)
from colophon.core.sources import (
    AUDIOBOOK_PROVIDERS,
    MetadataSource,
    SourceQuery,
    SourceResult,
    arrange_sources,
    unchecked_edition_fields,
)
from colophon.core.triage import has_blocking_error
from colophon.services import files as file_ops
from colophon.services import graph_inspect as graph_inspect_svc
from colophon.services.acquire import (
    RESOLVE_CONCURRENCY,
    AcquireCandidate,
    AcquireMode,
    AcquireResult,
    add_torrent,
    add_torrent_file,
    download_target_count,
    download_torrent,
    list_candidates,
    sanitize_name,
)
from colophon.services.catalog import apply_catalog_mapping
from colophon.services.cleanup import CleanupReport, find_cleanup_candidates
from colophon.services.combine import combine_books as _svc_combine
from colophon.services.combine import uncombine_books as _svc_uncombine
from colophon.services.cover import ensure_cached_cover, thumbnail_bytes
from colophon.services.editing import (
    apply_fields,
    bulk_apply_fields,
    bulk_remap_embedded_field,
    embedded_value,
    remap_field,
    set_field_value,
    swap_fields,
)
from colophon.services.editing import (
    bulk_normalize as _svc_bulk_normalize,
)
from colophon.services.editing import (
    bulk_remap as _svc_bulk_remap,
)
from colophon.services.editing import (
    bulk_set_field as _svc_bulk_set_field,
)
from colophon.services.editing import (
    bulk_swap_fields as _svc_bulk_swap,
)
from colophon.services.encode import encode_book
from colophon.services.graph_build import build_graph
from colophon.services.ingest import (
    ScanOptions,
    ScanPlan,
    commit_scan,
    plan_rescan_folders,
    plan_scan_graph,
    refresh_local,
    scan_ingest,
    sweep_missing,
)
from colophon.services.matching import gather_matches, query_for_book
from colophon.services.organize import OrganizeResult, organize_book, organize_book_parts
from colophon.services.tag_ops import (
    TagCommitResult,
    TagPlan,
    commit_tag,
    plan_tag,
    revert_tag_batch,
    tag_file,
)
from colophon.services.undo import undo_batch

logger = logging.getLogger(__name__)

_OP_ORGANIZE = "organize"  # audit-log op_type for a move into the library
_MATCH_CONCURRENCY = 8  # max books scanned concurrently during Identify/Quick Match
_REPROBE_COMMIT_BATCH = 200  # re-probe persists every N changed books, so progress survives a restart
# Author provenances that are derived from the folder classification (vs. the file's own tags,
# a match, a manual edit, or the filename). Only these are re-derived when a folder is reclassified,
# so a book tracks the current classification without ever clobbering authoritative author data.
_GRAPH_AUTHOR_PROV = frozenset({Provenance.GRAPHING.value, Provenance.DIRECTORY.value})


def _organize_fail_detail(org: OrganizeResult) -> str:
    """A readable reason for a failed organize move: a collision names the destination that already
    exists; otherwise the filesystem error (or a generic fallback)."""
    if org.collision:
        return f"a file already exists at the destination: {org.target_path}"
    return org.error or "the move into the library failed"


class CoverSetResult(_Base):
    ok: bool = False
    error: str | None = None


def _detect_image_ext(data: bytes) -> str | None:
    """'.jpg' / '.png' from the leading magic bytes, or None if not a JPEG/PNG."""
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


# Display labels for the metadata sources shown in the match-search dialog.
_SOURCE_LABELS = {
    "audnexus": "Audible",
    "openlibrary": "OpenLibrary",
    "googlebooks": "Google Books",
    "hardcover": "Hardcover",
    "internetarchive": "Internet Archive",
}


def _label_for(source: MetadataSource) -> str:
    """Human-facing label for a metadata source: its own `label` if set, else the
    known mapping, else a title-cased form of its name."""
    name = source.name
    return getattr(source, "label", None) or _SOURCE_LABELS.get(name, name.replace("_", " ").title())


class ChapterApplyResult(_Base):
    ok: bool = False
    count: int = 0
    audible_runtime_ms: int = 0
    source_runtime_ms: int = 0
    mismatch: bool = False
    error: str | None = None


class CatalogResult(_Base):
    affected_count: int = 0
    affected_ids: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    batch_id: str | None = None


class ProcessResult(_Base):
    book_id: str
    encoded: bool = False
    organized: bool = False
    detail: str | None = None


class DownloadEntry(_Base):
    key: str
    name: str
    status: str = "queued"   # queued / active / paused / done / partial / failed
    phase: str = ""          # while active: "resolving" | "downloading"
    detail: str = ""
    file_ids: list[int] | None = None  # chosen file subset (None = default audio+cover)
    files_total: int = 0     # Y: picked-file target, set once (not overwritten)
    files_done: int = 0      # files completed so far
    links_total: int = 0     # N: links to resolve (resolve-phase denominator)
    links_done: int = 0


class EncodeJobOptions(_Base):
    encode: bool = True
    organize: bool = True
    delete_sources: bool = False
    concurrency: int = 2
    patterns: PathPatterns | None = None  # per-run organize override; None = ctx.patterns


class BookProcessResult(_Base):
    book_id: str
    status: str = "queued"  # done / failed / cancelled / skipped
    detail: str | None = None


class EncodeJobResult(_Base):
    results: list[BookProcessResult] = []  # noqa: RUF012 - pydantic default, copied per instance


@dataclass(frozen=True)
class RerunResult:
    """Outcome of re-running a phase across a selection. `staled` is the union of
    downstream phases left stale by the cascade across all books; `failed` counts
    books whose target phase ended FAILED."""
    ran: Phase
    book_count: int
    staled: frozenset[Phase]
    failed: int


@dataclass(frozen=True)
class OrganizePreviewRow:
    """One Persist-preview row: where a book would organize, whether that target already
    exists (a collision that will be skipped), and whether a blocking error will skip it."""
    book_id: str
    title: str
    target: Path
    collision: bool
    blocked: bool


@dataclass(frozen=True)
class DeleteResult:
    files_deleted: int
    book_removed: bool
    errors: tuple[str, ...]


# Provenances that mean "auto-derived from the folder or filename" — the fields a re-identify
# should clear so the chosen pattern re-derives them. Everything else (tags, datafile, a match, a
# manual edit) is authoritative and left alone.
_WEAK_IDENTITY_PROV = frozenset({Provenance.DIRECTORY.value, Provenance.FILENAME.value})
_IDENTITY_LIST_FIELDS = frozenset({"authors", "narrators", "series", "genres", "tags"})


def _clear_weak_identity(book: BookUnit) -> None:
    """Reset every field whose current value came from the folder/filename (weak provenance) so a
    re-identify re-derives it from the chosen pattern. Hard fields are untouched. In place."""
    for field in list(book.provenance):
        if book.provenance.get(field) not in _WEAK_IDENTITY_PROV or not hasattr(book, field):
            continue
        if field in _IDENTITY_LIST_FIELDS:
            setattr(book, field, [])
        elif field == "publish_year":
            book.publish_year = None
        else:
            setattr(book, field, None)
        book.provenance.pop(field, None)


class AppController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._downloads: dict[str, DownloadEntry] = {}
        self._download_cancels: dict[str, CancelToken] = {}
        self._download_folders: dict[str, Path] = {}  # torrent id -> dest folder, so a resume reuses it
        self._rd_resolve_sem = asyncio.Semaphore(RESOLVE_CONCURRENCY)
        self._rd_download_sem = asyncio.Semaphore(
            max(1, self.ctx.config.real_debrid_max_concurrent_downloads))
        self.acquire_mode: AcquireMode = AcquireMode.INDEXED
        self._graph_cache: dict[tuple[str, bool], Graph] = {}  # diagnostic /graph: per (root, fresh)
        # Derived-view memos, valid while their input generations (books/aliases/graph) hold. Keyed
        # by those generations so any write rebuilds automatically — no manual invalidation to forget.
        self._tree_cache: tuple[tuple[int, int, int], LibraryTree] | None = None
        self._distinct_cache: dict[str, tuple[int, list[str]]] = {}  # kind -> (books_gen, values)
        self._classic_graph_cache: tuple[tuple[str, int, int], Graph] | None = None  # (root, graph_gen, books_gen)

    def save_settings(self, config: Config) -> None:
        """Persist `config` and update the live context. The source list is rebuilt
        from the full available set (re-discovering abs-agg) and arranged per the
        saved order/disabled prefs, so reorders, enable/disable, and abs-agg URL
        changes all take effect without a restart. (db_path changes need a restart.)"""
        scheme_changed = (
            self.ctx.config.filename_template != config.filename_template
            or self.ctx.config.directory_scheme != config.directory_scheme
        )
        save_config(config, self.ctx.config_path)
        self.ctx.config = config
        self.ctx.sources = arrange_sources(
            build_all_sources(config),
            order=config.source_order,
            disabled=config.disabled_sources,
        )
        if scheme_changed:
            for book in self.ctx.books.list_all():
                if not book.phases:
                    ensure_phases(book)
                invalidate_from(book, Phase.CATEGORIZE)   # stale; next scan re-runs local phases
                self.ctx.books.upsert(book)

    # --- scanning / identification ---
    def scan_preview(
        self, roots: list[Path] | None = None,
        *, template: str | None = None, directory_scheme: str | None = None,
        options: ScanOptions | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> ScanPlan:
        """Compute, without persisting, what a scan of `roots` (default: the configured
        scan paths) would do across all roots. `template`/`directory_scheme` override the
        saved defaults for this run (None = use config). `progress(done, total, label)`
        fires per folder (per root)."""
        template = template if template is not None else self.ctx.config.filename_template
        directory_scheme = (
            directory_scheme if directory_scheme is not None else self.ctx.config.directory_scheme
        )
        if options is not None and options.book_ids:
            # A selection-scoped rebuild re-scans the distinct FOLDERS its books live in through the
            # full graph scan, so a book in a multi-book folder is re-clustered and identified in
            # context instead of ballooning to own its whole folder.
            books = [b for b in (self.get_book(i) for i in options.book_ids) if b is not None]
            return plan_rescan_folders(
                self.ctx.books, [b.source_folder for b in books],
                options=options, template=template, directory_scheme=directory_scheme,
                inference_root_for=self._scan_root_for_path,
                node_overrides=self.ctx.overrides.all(),
                known_franchises=self.ctx.franchises.active(),
                single_book_folders=self.ctx.grouping.single_folders(),
                progress=progress,
            )
        roots = roots or self.ctx.config.scan_paths
        combined = ScanPlan()
        for root in roots:
            plan = plan_scan_graph(
                self.ctx.books, root, template=template, directory_scheme=directory_scheme,
                options=options, inference_root=self._scan_root_for_path(root), progress=progress,
                node_overrides=self.ctx.overrides.all(),
                known_franchises=self.ctx.franchises.active(),
                single_book_folders=self.ctx.grouping.single_folders(),
            )
            combined.units.extend(plan.units)
            combined.new_books += plan.new_books
            combined.existing_books += plan.existing_books
            combined.fields_filled += plan.fields_filled
            combined.files_added += plan.files_added
            combined.reconciled_folders |= plan.reconciled_folders
            combined.graph_nodes.extend(plan.graph_nodes)
            combined.graph_edges.extend(plan.graph_edges)
        return combined

    async def scan_preview_streamed(
        self, roots: list[Path] | None = None, *,
        template: str | None = None, directory_scheme: str | None = None,
        options: ScanOptions | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> ScanPlan:
        """Run scan_preview off the event loop, marshaling per-folder progress back onto
        it so a live UI indicator updates safely from the worker thread. Registers a shared
        job so the scan shows in every session's app-bar jobs indicator."""
        loop = asyncio.get_running_loop()

        with self.ctx.jobs.track("Scan") as job:
            def safe(done: int, total: int, label: str) -> None:
                job.progress(done, total, label)
                if progress is not None:
                    loop.call_soon_threadsafe(progress, done, total, label)

            return await asyncio.to_thread(
                self.scan_preview, roots,
                template=template, directory_scheme=directory_scheme,
                options=options, progress=safe,
            )

    def apply_scan(self, plan: ScanPlan) -> int:
        """Persist a previously-computed scan plan; returns the number written."""
        written = commit_scan(self.ctx.books, plan, graph_store=self.ctx.graph, reconcile=True)
        self._sync_library_graph(plan)
        # Sweep the whole catalog, not just this plan's folders: a folder that vanished
        # isn't walked by any scan, so a plan-scoped sweep would never see it. The
        # per-root accessibility guard keeps this cheap and false-positive-safe.
        sweep_missing(self.ctx.books, list(self.ctx.config.scan_paths))
        return written

    def _sync_library_graph(self, plan: ScanPlan) -> None:
        """Mirror what commit_scan persisted into the in-memory graph, per root. Partial
        rescan plans carry no records, so they leave the graph untouched (as commit_scan
        leaves the store untouched)."""
        if not plan.graph_nodes:
            return
        roots = {n.root for n in plan.graph_nodes}
        for root in roots:
            nodes = [n for n in plan.graph_nodes if n.root == root]
            edges = [e for e in plan.graph_edges if e.root == root]
            self.ctx.library_graph.replace_root(root, nodes, edges)

    def _resync_books(self, books: list[BookUnit]) -> None:
        """Re-derive the graph for every scan root the given books belong to."""
        self._resync_roots({self._scan_root_for_path(b.source_folder) for b in books})

    def _resync_roots(self, roots: set[Path]) -> int:
        """Keep each root's filesystem skeleton (unchanged by an edit) and re-derive its
        book/entity records from current books + overrides, then write through to the
        in-memory graph and the store. A root with no books and no skeleton is skipped
        (nothing to derive or keep); a book-only root is seeded from books (no skeleton
        until a scan).

        Each stored book is re-stamped with its local-identification confidence (rolled up
        from the just-reclassified graph) and its BookState re-derived; only books whose
        confidence or state actually changed are written back. Returns that changed count.

        Contract: callers must PERSIST the mutation (upsert/delete/override) BEFORE calling
        this — re-derivation reads `ctx.books.list_all()`/`ctx.overrides.all()`, not the
        passed objects."""
        if not roots:
            return 0
        books = self.ctx.books.list_all()
        overrides = self.ctx.overrides.all()
        lib = self.ctx.library_graph
        changed: list[BookUnit] = []
        for root in roots:
            r = str(root)
            skeleton_nodes = [
                n for n in lib.nodes.values() if n.root == r and n.physical in ("directory", "file")
            ]
            root_books = [
                b for b in books if self._scan_root_for_path(b.source_folder) == root
            ]
            if not root_books and not skeleton_nodes:
                continue  # truly empty root — nothing to derive or keep
            skeleton_edges = [
                e for e in lib.edges
                if e.root == r and e.kind == "contains" and not e.dst.startswith("book:")
            ]
            # First-pass franchise edges (from the current book field), used only to reconstruct
            # and classify the graph. The persisted edges are rebuilt post-fill below.
            franchise_of: dict[str, str] = {}
            for b in root_books:
                fname = resolve_book_franchise(
                    b, franchise_for(b.source_folder, overrides, root=root))
                if fname:
                    franchise_of[b.id] = fname
            book_nodes, book_edges = book_records(root_books, root=root, franchise_of=franchise_of)
            # Re-derive the directory classification in memory (no disk walk) so the maintained graph
            # carries current classification: rebuild the structural graph from the preserved skeleton
            # + fresh book records, reclassify, then re-serialize the skeleton with the new kinds. The
            # classify runs on book COPIES so its fill_down never mutates the stored books' own fields.
            copies = {b.id: b.model_copy(deep=True) for b in root_books}
            # A graph-derived author (filled from a folder classified `author`) must track the CURRENT
            # classification, not the one that first produced it: reclassifying that folder to Book must
            # re-home the book. `_fill_down` only adds an author and treats `graphing` as sticky, so clear
            # the copy's graph-derived author first — then it re-derives from the new kinds (landing empty
            # when no author ancestor remains). A hard (tag/datafile/match/manual) or filename author is
            # left untouched, so only these ids are written back below.
            graph_author_ids = {
                b.id for b in root_books
                if b.authors and b.provenance.get("authors") in _GRAPH_AUTHOR_PROV
            }
            for bid in graph_author_ids:
                copies[bid].authors = []
                copies[bid].provenance.pop("authors", None)
            recon = graph_from_records(
                skeleton_nodes + book_nodes, skeleton_edges + book_edges, copies, root=root,
            )
            classify_graph(recon, root=root)
            classify_nodes(recon, [bn.book for bn in recon.books.values()], root=root,
                           overrides=overrides, known_franchises=self.ctx.franchises.active(),
                           directory_scheme=self.ctx.config.directory_scheme)
            # Fill folder-derived franchise onto the STORED books before building the persisted
            # franchise edges, so the graph edge and book.franchise agree in one pass (a
            # declared/builtin franchise folder is only visible via the reclassified `recon`). Prefer
            # a node override's verbatim value over the classifier's (proper-cased) ancestor name.
            moved_ids: set[str] = set()
            for book in root_books:
                folder_fr = (franchise_for(book.source_folder, overrides, root=root)
                             or ancestor_franchise(recon, book.source_folder, root))
                if apply_franchise_fill(book, folder_fr):
                    moved_ids.add(book.id)
            # Write the re-derived graph author back onto the STORED book (mirrors the franchise fill
            # above). The copy reflects the new classification: a real ancestor author refills it, an
            # author-turned-Book folder clears it (dropping the book to "Needs identification").
            for book in root_books:
                if book.id not in graph_author_ids:
                    continue
                rederived = copies[book.id]
                if book.authors == rederived.authors:
                    continue
                book.authors = list(rederived.authors)
                new_prov = rederived.provenance.get("authors")
                if new_prov:
                    book.provenance["authors"] = new_prov
                else:
                    book.provenance.pop("authors", None)
                moved_ids.add(book.id)
            # Second pass: rebuild the franchise edges from the now-filled books, then serialize.
            franchise_of = {}
            for b in root_books:
                fname = resolve_book_franchise(
                    b, franchise_for(b.source_folder, overrides, root=root)
                    or ancestor_franchise(recon, b.source_folder, root))
                if fname:
                    franchise_of[b.id] = fname
            book_nodes, book_edges = book_records(root_books, root=root, franchise_of=franchise_of)
            sk_nodes, sk_edges = skeleton_records(recon, root=root)
            nodes = sk_nodes + book_nodes
            # Drop edges to skeleton nodes the preserved skeleton doesn't have — a book whose
            # source paths drifted (match/organize) would otherwise re-emit a dangling owns/contains.
            edges = prune_dangling_edges(nodes, sk_edges + book_edges)
            # Store first: if the persist raises (e.g. a write conflict), leave the
            # in-memory graph unchanged so the two never diverge.
            self.ctx.graph.replace_subgraph(root, nodes, edges)
            lib.replace_root(r, nodes, edges)
            # Stamp local-identification confidence + re-derive state onto the STORED books from the
            # freshly-reclassified graph (classify ran on copies, so the stored books keep their
            # fields; only these two derived caches move). Collect the movers to write.
            for book in root_books:
                old_ic, old_state = book.identity_confidence, book.state
                book.identity_confidence = book_identity_confidence(book, recon, root)
                resync_state(book, ready_threshold=self.ctx.config.review_threshold)
                if book.id in moved_ids or book.identity_confidence != old_ic or book.state is not old_state:
                    changed.append(book)
        for i, book in enumerate(changed):
            self.ctx.books.upsert(book, commit=(i == len(changed) - 1))
        return len(changed)

    def recompute_all_identity(self) -> int:
        """One-time backfill: re-derive every scan root's classification and stamp
        identity_confidence + BookState onto the stored books, writing back only the movers.
        Returns the number of books updated. Idempotent — a harmonized library writes nothing."""
        roots = {
            self._scan_root_for_path(b.source_folder) for b in self.ctx.books.list_all()
        }
        return self._resync_roots(roots)

    def reprobe_durations(self, *, only_missing: bool = True) -> int:
        """Re-read source-file durations from disk and reconcile the EMPTY_AUDIO finding, persisting
        any book that changed. With `only_missing` (default) limits to books that currently have a
        file with a nonzero size but zero duration — a file that read as 0 (before the ffprobe
        fallback existed, or a since-completed download). A file that recovers gets its real duration
        and loses the flag; a file still reading 0 with real size gains an EMPTY_AUDIO finding
        (corrupt / incomplete). Clears the audio cache so an unchanged file is genuinely re-read.
        Returns the number of books updated."""
        from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata
        from colophon.core.classify import empty_audio_finding

        def wants(book: BookUnit) -> bool:
            return any(sf.duration_seconds <= 0 < sf.size for sf in book.source_files)

        clear_audio_metadata_cache()
        targets = [b for b in self.ctx.books.list_all() if not only_missing or wants(b)]
        changed = 0
        pending: list[BookUnit] = []

        def flush() -> None:
            # Commit the accumulated batch (commit on the last upsert flushes the group). Batched so
            # a long run persists incrementally — a restart mid-run keeps what's done, not nothing.
            for i, b in enumerate(pending):
                self.ctx.books.upsert(b, commit=(i == len(pending) - 1))
            pending.clear()

        with self.ctx.jobs.track("Re-probe durations") as job:
            for n, book in enumerate(targets, start=1):
                job.progress(n, len(targets), book.title or book.source_folder.name)
                new_files = list(book.source_files)
                moved = False
                for i, sf in enumerate(book.source_files):
                    if sf.duration_seconds > 0 or not sf.path.exists():
                        continue
                    fresh = read_audio_metadata(sf.path)[0]
                    if fresh.duration_seconds != sf.duration_seconds:
                        new_files[i] = fresh
                        moved = True
                # Reconcile the EMPTY_AUDIO finding against the (possibly refreshed) files.
                finding = empty_audio_finding([(sf.size, sf.duration_seconds) for sf in new_files])
                others = [f for f in book.findings if f.code is not FindingCode.EMPTY_AUDIO]
                new_findings = others + ([finding] if finding is not None else [])
                if moved or new_findings != book.findings:
                    book.source_files = new_files
                    book.findings = new_findings
                    book.touch()
                    pending.append(book)
                    changed += 1
                if len(pending) >= _REPROBE_COMMIT_BATCH:
                    flush()
            flush()
        return changed

    def reprobe_book(self, book: BookUnit) -> bool:
        """Re-read one book's file durations from disk and reconcile its EMPTY_AUDIO finding,
        persisting if anything changed. For the At-a-Glance 'Re-probe' action after a user has
        replaced a corrupt file. Returns True when the book was updated."""
        from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata
        from colophon.core.classify import empty_audio_finding

        clear_audio_metadata_cache()
        new_files = list(book.source_files)
        moved = False
        for i, sf in enumerate(book.source_files):
            if sf.duration_seconds > 0 or not sf.path.exists():
                continue
            fresh = read_audio_metadata(sf.path)[0]
            if fresh.duration_seconds != sf.duration_seconds:
                new_files[i] = fresh
                moved = True
        finding = empty_audio_finding([(sf.size, sf.duration_seconds) for sf in new_files])
        others = [f for f in book.findings if f.code is not FindingCode.EMPTY_AUDIO]
        new_findings = others + ([finding] if finding is not None else [])
        if not (moved or new_findings != book.findings):
            return False
        book.source_files = new_files
        book.findings = new_findings
        book.touch()
        self.ctx.books.upsert(book)
        return True

    def active_jobs(self) -> list[Job]:
        """Snapshot of running background jobs, for the app-bar indicator (shared across sessions)."""
        return self.ctx.jobs.active()

    def reconcile_graph(self) -> int:
        """Self-heal: drop graph content that can no longer be valid — book nodes whose book was
        deleted, and nodes/edges on roots that are no longer scan paths — then persist the cleaned
        per-root subgraphs. Cures leftovers from a removed/renamed scan path (whose old-root
        subgraph `replace_subgraph` never revisits) and from book deletions. Returns the number of
        nodes removed; a no-op on a healthy graph. Never purges when no scan paths are configured,
        so a transient empty config can't wipe the whole graph."""
        active = {str(p) for p in self.ctx.config.scan_paths}
        if not active:
            return 0
        book_ids = {b.id for b in self.ctx.books.list_all()}
        result = reconcile(self.ctx.library_graph, active_roots=active, book_ids=book_ids)
        if not result:
            return 0
        for r in result.affected_roots:
            nodes_r = [n for n in self.ctx.library_graph.nodes.values() if n.root == r]
            edges_r = [e for e in self.ctx.library_graph.edges if e.root == r]
            self.ctx.graph.replace_subgraph(Path(r), nodes_r, edges_r)
        logger.info(
            f"graph reconcile: removed {len(result.removed_node_ids)} orphan node(s) and "
            f"{result.removed_edges} dangling edge(s) across {len(result.affected_roots)} root(s)"
        )
        return len(result.removed_node_ids)

    def rebuild_missing_graph(self) -> int:
        """Self-heal: for any book not represented in the in-memory graph, rebuild its
        scan root's entity records from the existing books (no scan, no filesystem walk,
        no book changes). Returns the number of roots rebuilt. Idempotent — a healthy
        graph rebuilds nothing."""
        books = self.ctx.books.list_all()
        present = set(self.ctx.library_graph.nodes)
        missing_roots = {
            self._scan_root_for_path(b.source_folder)
            for b in books
            if book_node_id(b.id) not in present
        }
        if missing_roots:
            self._resync_roots(missing_roots)
        return len(missing_roots)

    def scan_paths_missing_graph(self) -> list[Path]:
        """Configured scan paths with no subgraph in the in-memory graph (never scanned /
        not yet persisted). NOTE: a folder that scans to zero books persists no graph
        nodes, so it keeps being reported here; the workspace's once-per-process guard
        (not this method) is what stops an empty/unscannable path from re-scanning in a
        loop."""
        present = {n.root for n in self.ctx.library_graph.nodes.values()}
        return [p for p in self.ctx.config.scan_paths if str(p) not in present]

    def cleanup_report(self) -> CleanupReport:
        """Review-only: bucket persisted books whose files are gone from disk or no
        longer under any scan path. Computes nothing destructive."""
        return find_cleanup_candidates(self.ctx.books.list_all(), self.ctx.config.scan_paths)

    def cleanup_remove(self, book_ids: Iterable[str]) -> int:
        """Delete the given stale books and their satellite rows (edit history,
        operations) in one transaction, then re-derive the affected graph roots so
        the removed books' nodes and edges are pruned. Entity aliases, known
        entities and node overrides are left untouched — user-declared, not
        file-derived. Returns the number of books removed."""
        ids = list(book_ids)
        if not ids:
            return 0
        removal = set(ids)
        roots = {
            Path(n.root)
            for n in self.ctx.library_graph.nodes.values()
            if n.attrs.get("book_id") in removal
        }
        last = len(ids) - 1
        for i, bid in enumerate(ids):
            self.ctx.history.delete_for_book(bid, commit=False)
            self.ctx.operations.delete_for_book(bid, commit=False)
            self.ctx.books.delete(bid, commit=(i == last))  # final delete flushes the batch
        self._resync_roots(roots)
        return len(ids)

    def remove_from_library(self, book_ids: Iterable[str]) -> int:
        """Drop books from Colophon after they've been organized to their destination:
        the record + edit history + operations + graph nodes go, but their organized output
        files (and their source originals) are left on disk. Deleting files is the separate,
        independent delete-sources concern. Returns the number removed."""
        return self.cleanup_remove(book_ids)

    def remove_missing(self, book: BookUnit) -> None:
        """Delete an orphaned (missing) book record and its history/operations rows.
        The three deletes share one transaction (commit on the last) so the record and
        its satellite rows can't be left half-removed."""
        root = self._scan_root_for_path(book.source_folder)
        self.ctx.history.delete_for_book(book.id, commit=False)
        self.ctx.operations.delete_for_book(book.id, commit=False)
        self.ctx.books.delete(book.id)  # commits, flushing the two preceding deletes
        self._resync_roots({root})

    def delete_corrupt_files(self, book: BookUnit) -> DeleteResult:
        """Permanently delete this book's corrupt/incomplete files (real size, no readable audio),
        drop them from the book, and re-derive its EMPTY_AUDIO finding. When nothing playable is
        left the whole book is removed (record + satellites + graph). Irreversible; the UI gates it
        behind a confirm dialog."""
        from colophon.core.classify import corrupt_source_files, empty_audio_finding
        from colophon.services.files import delete_files_from_disk, exclude

        targets = corrupt_source_files(book.source_files)
        removed = delete_files_from_disk(targets)
        removed_set = set(removed)
        errors = [f"could not delete {p.name}" for p in targets if p not in removed_set]
        for p in removed:
            exclude(book, p)

        if not book.source_files:
            self.cleanup_remove([book.id])
            return DeleteResult(files_deleted=len(removed), book_removed=True, errors=tuple(errors))

        finding = empty_audio_finding([(sf.size, sf.duration_seconds) for sf in book.source_files])
        others = [f for f in book.findings if f.code is not FindingCode.EMPTY_AUDIO]
        book.findings = others + ([finding] if finding is not None else [])
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        book.touch()
        self.ctx.books.upsert(book)
        self._resync_roots({self._scan_root_for_path(book.source_folder)})
        return DeleteResult(files_deleted=len(removed), book_removed=False, errors=tuple(errors))

    def scan(self, roots: list[Path] | None = None, *, options: ScanOptions | None = None) -> int:
        """Convenience: preview then immediately commit. Returns the count."""
        return self.apply_scan(self.scan_preview(roots, options=options))

    def _scan_root_for_path(self, path: Path) -> Path:
        """The configured scan path that contains (or equals) `path`, else `path` itself."""
        for root in self.ctx.config.scan_paths:
            if path == root or root in path.parents:
                return root
        return path

    def _root_for(self, book: BookUnit) -> Path:
        """The configured scan root that contains `book`, for re-running local phases.
        For a book outside every scan path, fall back to its parent (best-effort one-level
        directory inference) rather than the folder itself, which would infer nothing."""
        root = self._scan_root_for_path(book.source_folder)
        return root if root != book.source_folder else book.source_folder.parent

    def invalidate(self, book: BookUnit, from_phase: Phase, *, template: str | None = None) -> None:
        """Invalidate `from_phase` forward, auto-rerun the local phases, persist.
        Deferred phases are left stale for an explicit run. `template` overrides the global
        filename template for this run (a per-operation re-identify)."""
        if not book.phases:
            ensure_phases(book)
        invalidate_from(book, from_phase)
        refresh_local(
            book,
            root=self._root_for(book),
            template=template or self.ctx.config.filename_template,
            directory_scheme=self.ctx.config.directory_scheme,
        )
        self.ctx.books.upsert(book)

    def _hydrate(self, books: list[BookUnit]) -> list[BookUnit]:
        """Seed the phase map on legacy books that have an empty `phases` dict.
        Seeding is in-memory only; the next upsert of each book will persist it."""
        for b in books:
            if not b.phases:
                ensure_phases(b)
        return books

    def books_by_state(self, state: BookState) -> list[BookUnit]:
        """Books whose derived BookState equals `state` (legacy rows hydrated)."""
        return [b for b in self._hydrate(self.ctx.books.list_all()) if b.state is state]

    def books_with_phase(self, phase: Phase, status: PhaseState) -> list[BookUnit]:
        """Books whose `phase` is in `status` (legacy rows hydrated first)."""
        return [b for b in self._hydrate(self.ctx.books.list_all())
                if state_of(b, phase) is status]

    def phase_membership(self, books: list[BookUnit]) -> dict[Phase, list[BookUnit]]:
        """Group `books` by phase: each phase maps to the subset whose phase is FRESH.
        A book appears under every FRESH phase (cumulative funnel). One pass; pure over
        the supplied list (the caller applies any folder/scope filtering)."""
        out: dict[Phase, list[BookUnit]] = {p: [] for p in Phase}
        for book in books:
            for phase in Phase:
                if state_of(book, phase) is PhaseState.FRESH:
                    out[phase].append(book)
        return out

    def rerun_phase(self, books: list[BookUnit], phase: Phase) -> RerunResult:
        """Re-run `phase` for each book and report the cascade. Local phases
        (Search/Categorize/Identify) cascade-invalidate and auto-rerun via invalidate();
        downstream deferred phases are left stale for their own explicit re-run. Deferred
        phases are not wired here — their homes live elsewhere — and raise
        NotImplementedError. `RerunResult.staled` is the union of downstream phases left
        stale across the selection."""
        if phase not in LOCAL:
            raise NotImplementedError(f"re-run not yet wired for deferred phase {phase.value}")
        staled: set[Phase] = set()
        failed = 0
        for book in books:
            self.invalidate(book, phase)
            staled |= {p for p in phases_from(phase)[1:] if state_of(book, p) is PhaseState.STALE}
            if state_of(book, phase) is PhaseState.FAILED:
                failed += 1
        return RerunResult(
            ran=phase, book_count=len(books), staled=frozenset(staled), failed=failed,
        )

    def reidentify(self, books: list[BookUnit], *, template: str | None = None) -> int:
        """Re-identify `books` locally with `template` (default: the global filename template).
        Clears each book's weak (folder/filename-derived) fields, then re-runs IDENTIFY so they
        re-derive from the chosen pattern. Hard fields (tag/datafile/match/manual) are preserved.
        A supplied `template` is added to the recent-template history; the global default is
        unchanged. Returns the number of books re-identified."""
        hydrated = self._hydrate(books)
        for book in hydrated:
            _clear_weak_identity(book)
            self.invalidate(book, Phase.IDENTIFY, template=template)  # clears+re-derives+upserts
        if template:
            self.record_filename_template(template)
        self._resync_roots({self._scan_root_for_path(b.source_folder) for b in hydrated})
        return len(hydrated)

    # --- dashboard ---
    @timed("dashboard_stats")
    def dashboard_stats(self) -> dict[str, int]:
        books = self._hydrate(self.ctx.books.list_all())
        stats = {"total": len(books)}
        for state in BookState:
            stats[state.value] = sum(1 for b in books if b.state == state)
        return stats

    def get_book(self, book_id: str) -> BookUnit | None:
        book = self.ctx.books.get(book_id)
        return self._hydrate([book])[0] if book is not None else None

    def graph_search(self, query: str) -> list[dict]:
        """Focal candidates for the explorer search box: [{id, label, kind}]."""
        return graph_inspect_svc.search(self.ctx.library_graph, self.ctx.books, query)

    def graph_neighborhood(
        self, focal_id: str, *, hops: int = 1, hidden: frozenset[str] = frozenset()
    ) -> dict:
        """The ECharts options + omitted count for `focal_id`'s `hops`-hop neighborhood (chart only;
        the focal details are a separate `graph_inspect` call)."""
        return graph_inspect_svc.neighborhood_view(
            self.ctx.library_graph, self.ctx.books, focal_id, depth=hops, hidden=hidden)

    def graph_inspect(self, focal_id: str):
        """The per-kind inspect read-model for the panel (rows, linked folders, files, provenance,
        links)."""
        return graph_inspect_svc.inspect(self.ctx.library_graph, self.ctx.books, focal_id)

    async def book_cover(self, book_id: str, *, thumb: bool = False) -> tuple[bytes, str] | None:
        """A book's cover image as (bytes, mime): the cached file if present, else
        fetched from `cover_url` and cached for next time. With `thumb`, serve a
        small downscaled JPEG (for the list/navigator rows) instead of the
        full-size image, falling back to full if it can't be thumbnailed. None when
        the book has no cover or the fetch fails."""
        book = self.get_book(book_id)
        if book is None:
            return None
        source = None
        if book.cover_path and book.cover_path.exists():
            source = book.cover_path
        elif book.cover_url:
            source = await ensure_cached_cover(book, dest_dir=book.source_folder)
            if source is not None:
                self.ctx.books.upsert(book)  # remember the cache location
        if source is None:
            return None
        if thumb:
            tn = await asyncio.to_thread(thumbnail_bytes, source)
            if tn is not None:
                return tn
        return source.read_bytes(), mime_for_suffix(source)
    async def ensure_cover_cached(self, book: BookUnit) -> None:
        """Cache the book's cover_url into cover_path (if not already cached) so a
        synchronous encode can embed it. No-op when a cached cover exists or there
        is no cover_url."""
        if book.cover_path and book.cover_path.exists():
            return
        path = await ensure_cached_cover(book, dest_dir=book.source_folder)
        if path is not None:
            self.ctx.books.upsert(book)

    def set_cover_url(self, book: BookUnit, url: str) -> None:
        """Point the book at a new cover URL, clearing any cached file so the new
        image is fetched + served on demand."""
        book.cover_url = (url or "").strip() or None
        book.cover_path = None
        book.touch()
        self.ctx.books.upsert(book)

    def set_cover_upload(
        self, book: BookUnit, data: bytes, filename: str | None = None
    ) -> CoverSetResult:
        """Write an uploaded JPEG/PNG to the book's folder as cover.<ext> and use
        it (clearing cover_url). Rejects non-image bytes."""
        ext = _detect_image_ext(data)
        if ext is None:
            return CoverSetResult(ok=False, error="Not a JPEG or PNG image")
        # Per-book name (not a folder-shared "cover.<ext>") so clustered books that
        # share this folder don't overwrite each other's uploaded cover.
        path = book.source_folder / f"cover-{book.id}{ext}"
        try:
            book.source_folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            logger.warning(f"writing cover to {path} failed: {e}")
            return CoverSetResult(ok=False, error="Could not write cover file")
        book.cover_path = path
        book.cover_url = None
        book.touch()
        self.ctx.books.upsert(book)
        return CoverSetResult(ok=True)

    def set_abridged(self, book: BookUnit, value: bool | None) -> None:
        """Set the abridged flag directly (None = unknown) and persist. Not part of
        the undoable field history."""
        book.abridged = value
        book.touch()
        self.ctx.books.upsert(book)

    async def cover_candidates(self, book: BookUnit) -> list[str]:
        """Distinct cover URLs from a match search, best-ranked first."""
        out: list[str] = []
        seen: set[str] = set()
        for r in await self.get_matches(book):
            if r.cover_url and r.cover_url not in seen:
                seen.add(r.cover_url)
                out.append(r.cover_url)
        return out

    def clear_cover(self, book: BookUnit) -> None:
        """Remove the book's cover (both URL and cached file reference)."""
        book.cover_url = None
        book.cover_path = None
        book.touch()
        self.ctx.books.upsert(book)

    def dedupe_colliding_covers(self) -> int:
        """One-time repair for covers cached before the fix that keyed the cache file
        on the folder. Clustered books that share a source folder all wrote to the same
        `cover.<ext>`, so any cover_path value held by more than one book is a collision:
        all but one book points at the wrong image. Clear cover_path on every book whose
        cover_path is shared, so each re-fetches its own cover_url into its per-book path.
        A shared cover with no cover_url loses an already-incorrect image. Idempotent —
        once healed, per-book paths are unique and this writes nothing. Returns the count
        cleared."""
        books = self.ctx.books.list_all()
        counts = Counter(str(b.cover_path) for b in books if b.cover_path is not None)
        shared = {p for p, n in counts.items() if n > 1}
        stale = [b for b in books if b.cover_path is not None and str(b.cover_path) in shared]
        for i, book in enumerate(stale):
            book.cover_path = None
            book.touch()
            self.ctx.books.upsert(book, commit=(i == len(stale) - 1))
        return len(stale)

    def books_all(self) -> list[BookUnit]:
        """All persisted books (used by callers that need the full set)."""
        return self.ctx.books.list_all()

    def _distinct(self, kind: str, project: Callable[[BookUnit], Iterable[str]]) -> list[str]:
        """Sorted distinct values projected from every book (editor autocomplete), memoized per
        `kind` against the book-store generation so repeated detail-pane opens don't rescan the
        whole library. The cached list is read-only (callers must not mutate it)."""
        gen = self.ctx.books.generation
        cached = self._distinct_cache.get(kind)
        if cached is not None and cached[0] == gen:
            return cached[1]
        values = sorted({v for b in self.ctx.books.list_all() for v in project(b)})
        self._distinct_cache[kind] = (gen, values)
        return values

    def known_authors(self) -> list[str]:
        """Distinct author names across the library, sorted (editor autocomplete)."""
        return self._distinct("authors", lambda b: b.authors)

    def known_series(self) -> list[str]:
        """Distinct series names across the library, sorted (editor autocomplete)."""
        return self._distinct("series", lambda b: (s.name for s in b.series))

    def genre_policy(self) -> GenrePolicy:
        """Build the active genre policy from config."""
        return GenrePolicy(
            mapping=self.ctx.config.genre_mapping,
            accepted=self.ctx.config.accepted_genres,
            whitelist_enabled=self.ctx.config.genre_whitelist_enabled,
        )

    def known_genres(self) -> list[str]:
        """Distinct genre names across the library, sorted (editor autocomplete)."""
        return self._distinct("genres", lambda b: b.genres)

    @timed("catalog_entries")
    def catalog_entries(self, kind: str) -> list[CatalogEntry]:
        """Distinct values of `kind` across the whole library, with usage counts."""
        return list_entries(self.ctx.books.list_all(), kind)

    def _catalog_apply(self, kind: str, mapping: dict[str, str | None]) -> CatalogResult:
        affected, batch_id = apply_catalog_mapping(self.ctx.books, self.ctx.history, kind, mapping)
        return CatalogResult(affected_count=len(affected), affected_ids=affected, batch_id=batch_id)

    def rename_catalog_entry(self, kind: str, old: str, new: str) -> CatalogResult:
        return self._catalog_apply(kind, {old: new})

    def merge_catalog_entries(self, kind: str, sources: list[str], target: str) -> CatalogResult:
        return self._catalog_apply(kind, {s: target for s in sources})

    def delete_catalog_entry(self, kind: str, name: str) -> CatalogResult:
        return self._catalog_apply(kind, {name: None})

    def known_tags(self) -> list[str]:
        """Distinct tag names across the library, sorted (editor autocomplete)."""
        return self._distinct("tags", lambda b: b.tags)

    # --- workspace navigator ---
    @timed("library_tree")
    def library_tree(self) -> LibraryTree:
        """Group all books into the entity-model tree, read from the maintained graph
        (`ctx.library_graph`). Conservative: `all_books`/`needs_id` come from `ctx.books`,
        so a book the graph hasn't placed still shows (in All, and under Needs
        identification) rather than vanishing."""
        key = (
            self.ctx.books.generation,
            self.ctx.aliases.generation,
            self.ctx.library_graph.generation,
        )
        if self._tree_cache is not None and self._tree_cache[0] == key:
            return self._tree_cache[1]
        books = self._hydrate(self.ctx.books.list_all())
        books_by_id = {b.id: b for b in books}
        aliases = self.ctx.aliases.all()
        entity_graph = entity_graph_from_records(self.ctx.library_graph, books_by_id, aliases=aliases)
        tree = build_library_tree(books, aliases=aliases, entity_graph=entity_graph)
        self._tree_cache = (key, tree)
        return tree

    def library_tree_warm(self) -> bool:
        """True when the memoized library tree is valid for the current generation
        key, so the caller can build synchronously instead of deriving off-thread."""
        key = (
            self.ctx.books.generation,
            self.ctx.aliases.generation,
            self.ctx.library_graph.generation,
        )
        return self._tree_cache is not None and self._tree_cache[0] == key

    def navigator_view(self, book_ids: set[str] | None = None) -> LibraryTree:
        """The navigator tree narrowed to `book_ids` (the books currently visible in the list), or
        the full tree when None. The query seam: the UI passes the shared filter's match set so the
        navigator and the book list always show the same subset. Reads the memoized `library_tree`,
        so narrowing never triggers a rebuild; the in-memory filter can move to a SQL/indexed query
        later without a UI change."""
        return filter_library_tree(self.library_tree(), book_ids)

    def list_directory(self, path: Path) -> DirectoryListing:
        """List a directory's immediate children: subdirs first, then files.

        Returns an empty listing if the path is absent or not a directory."""
        if not path.is_dir():
            return DirectoryListing(path=path, entries=[])
        try:
            children = list(path.iterdir())
        except OSError:
            return DirectoryListing(path=path, entries=[])
        dirs = sorted((c for c in children if c.is_dir()), key=lambda c: c.name.casefold())
        files = sorted((c for c in children if c.is_file()), key=lambda c: c.name.casefold())
        entries = [DirEntry(path=c, name=c.name, is_dir=True, is_audio=False) for c in dirs]
        entries += [
            DirEntry(path=c, name=c.name, is_dir=False, is_audio=is_audio_file(c)) for c in files
        ]
        return DirectoryListing(path=path, entries=entries)

    # --- editing / undo ---
    # NOTE: colophon does not write metadata.json — that file is AudiobookShelf's domain.
    # `adapters.sidecar.write_datafile_sidecar` is kept for a future, explicit "export to ABS"
    # utility, but is deliberately not called as an edit-time side effect anymore.
    def edit_field(self, book: BookUnit, field: str, value: str | None) -> str:
        batch = set_field_value(self.ctx.books, self.ctx.history, book, field, value)
        self.invalidate(book, Phase.TAG)
        return batch

    def save_fields(self, book: BookUnit, updates: dict[str, str | None]) -> str:
        """Apply manual metadata edits to `book` in one batch and re-sync its
        datafile sidecar. Returns the batch id (undoable via undo)."""
        batch = apply_fields(
            self.ctx.books, self.ctx.history, book, updates, provenance=Provenance.MANUAL.value
        )
        self.invalidate(book, Phase.TAG)
        self._resync_books([book])
        return batch

    # --- Real-Debrid acquisition (issue #11) ---
    def rd_configured(self) -> bool:
        return bool(self.ctx.config.real_debrid_token)

    def rd_client(self) -> RealDebridSource:
        """The Real-Debrid source used for acquisition: the live client wrapped in the
        persistent cache, so repeat picks and page loads reuse prior responses."""
        token = self.ctx.config.real_debrid_token
        if not token:
            raise ValueError("no Real-Debrid token configured")
        return CachingRealDebridSource(RealDebridClient(token), self.ctx.rd_cache)

    def _rd_download_dir(self) -> Path:
        return self.ctx.config.real_debrid_download_dir or (default_db_path().parent / "downloads")

    async def rd_test_connection(self, token: str | None = None) -> RdUser:
        """Verify the RD token by fetching the account. Tests `token` if given
        (without persisting it), else the configured one. Lets the Settings page
        validate a typed-but-unsaved token without mutating live config."""
        token = token or self.ctx.config.real_debrid_token
        if not token:
            raise ValueError("no Real-Debrid token configured")
        client = RealDebridClient(token)
        try:
            return await client.user()
        finally:
            await client.aclose()

    async def rd_list_candidates(self) -> list[AcquireCandidate]:
        client = self.rd_client()
        try:
            return await list_candidates(client)
        finally:
            await client.aclose()

    async def rd_add_magnet(self, magnet: str, *, audio_only: bool = False) -> str:
        """Add a magnet to Real-Debrid and select its files. Returns the torrent id."""
        client = self.rd_client()
        try:
            return await add_torrent(client, magnet, audio_only=audio_only)
        finally:
            await client.aclose()

    async def rd_add_torrent_file(self, content: bytes, *, audio_only: bool = False) -> str:
        """Upload a .torrent file to Real-Debrid and select its files. Returns the torrent id."""
        client = self.rd_client()
        try:
            return await add_torrent_file(client, content, audio_only=audio_only)
        finally:
            await client.aclose()

    async def rd_refresh_cache(self) -> None:
        """Force-rescan Real-Debrid, repopulating the cache: a fresh torrent list (which
        prunes removed torrents) plus a forced torrent_info for every ready torrent."""
        client = self.rd_client()
        try:
            torrents = await client.list_torrents()
            for t in torrents:
                if t.status in ("downloaded", "uploading"):  # only ready torrents are cacheable
                    try:
                        await client.torrent_info(t.id, force=True)
                    except Exception as e:  # one torrent failing must not abort the refresh (BLE001 intentional)
                        logger.warning(f"refresh: torrent_info failed for {t.id}: {e}")
        finally:
            await client.aclose()

    def active_downloads(self) -> list[DownloadEntry]:
        """Every tracked download (queued / active / paused / done / partial / failed)."""
        return list(self._downloads.values())

    def clear_finished_downloads(self) -> None:
        """Drop the finished (done / failed / partial) entries and their cancel tokens."""
        for key in [k for k, e in self._downloads.items() if e.status in ("done", "failed", "partial")]:
            self._downloads.pop(key, None)
            self._download_cancels.pop(key, None)
            self._download_folders.pop(key, None)

    def pause_download(self, key: str) -> None:
        """Stop an in-flight download but keep its .part files for a later resume."""
        token = self._download_cancels.get(key)
        if token is not None:
            token.cancel()

    def cancel_download(self, key: str) -> None:
        """Abort a download and discard it: signal any in-flight token, delete the retained
        partial (.part) files, remove an emptied container, and drop the entry. Deleting only
        the partials (not the whole folder unless it is now empty) keeps a book-folder fix's
        other files intact."""
        token = self._download_cancels.get(key)
        if token is not None:
            token.cancel()
        folder = self._download_folders.get(key)
        if folder is not None and folder.exists():
            for part in folder.rglob("*.part"):
                part.unlink(missing_ok=True)
            try:
                folder.rmdir()  # only removes it if now empty
            except OSError:
                pass
        self._downloads.pop(key, None)
        self._download_cancels.pop(key, None)
        self._download_folders.pop(key, None)

    async def _run_download(
        self, torrent_id: str, name: str,
        *, file_ids: list[int] | None = None,
        progress: Callable[[str, int, int, str], None] | None = None,
        dest_dir: Path | None = None,
        mode: AcquireMode = AcquireMode.INDEXED,
    ) -> tuple[AcquireResult, list[str]]:
        """Download one torrent and ingest its folder, tracking progress/status in the
        registry. The entry is registered as 'queued' and starts 'active' once a
        download slot frees (at most `real_debrid_max_concurrent_downloads` run at once).
        A pause leaves it 'paused' (its .part files retained for resume); otherwise it
        ends 'done', 'partial', or 'failed'. `file_ids` restricts to a chosen subset
        (stored so a resume re-applies it); `dest_dir` overrides the download location."""
        entry = DownloadEntry(key=torrent_id, name=name, status="queued", file_ids=file_ids)
        self._downloads[torrent_id] = entry
        token = CancelToken()
        self._download_cancels[torrent_id] = token
        async with self._rd_download_sem:
            if token.cancelled:  # cancelled while waiting in the queue
                entry.status = "paused"
                return AcquireResult(folder=dest_dir or self._rd_download_dir()), []
            entry.status, entry.phase = "active", "resolving"
            return await self._run_download_active(
                entry, torrent_id, token, file_ids=file_ids, progress=progress,
                dest_dir=dest_dir, mode=mode)

    async def _run_download_active(
        self, entry: DownloadEntry, torrent_id: str, token: CancelToken,
        *, file_ids: list[int] | None,
        progress: Callable[[str, int, int, str], None] | None,
        dest_dir: Path | None,
        mode: AcquireMode,
    ) -> tuple[AcquireResult, list[str]]:
        """Run one download that has acquired a slot: resolve + stream, ingest, and set
        the terminal status. Assumes `entry`/`token` are already registered."""
        def _byte_progress(done: int, total: int) -> None:
            entry.detail = f"{done * 100 // total}%" if total else f"{done} bytes"

        def _prog(phase: str, done: int, total: int, fname: str) -> None:
            entry.phase = phase
            if phase == "resolving":
                entry.links_done, entry.links_total = done, total
            else:  # downloading
                entry.files_done = done  # files_total is fixed below; don't overwrite
                if fname:
                    entry.detail = fname
            if progress is not None:
                progress(phase, done, total, fname)

        client = self.rd_client()
        try:
            info = await client.torrent_info(torrent_id)
            entry.files_total = download_target_count(
                info, set(file_ids) if file_ids is not None else None)
            result = await download_torrent(
                client, info, dest_dir or self._rd_download_dir(),
                folder=self._download_folders.get(torrent_id),
                file_ids=set(file_ids) if file_ids is not None else None,
                progress=_prog, byte_progress=_byte_progress, cancel=token,
                mode=mode, resolve_sem=self._rd_resolve_sem,
            )
        finally:
            await client.aclose()
        # remember the folder so a later resume reuses it (and finds the retained .part)
        self._download_folders[torrent_id] = result.folder

        cancelled = any((f.error == "cancelled") for f in result.files)
        book_ids: list[str] = []
        if result.any_ok:
            books = scan_ingest(
                self.ctx.books, result.folder,
                template=self.ctx.config.filename_template,
                directory_scheme=self.ctx.config.directory_scheme,
            )
            book_ids = [b.id for b in books]
        ok_count = sum(1 for f in result.files if f.ok)
        if cancelled:
            entry.status = "paused"
        elif ok_count == 0:
            entry.status = "failed"
        elif ok_count < entry.files_total:
            entry.status = "partial"
        else:
            entry.status = "done"
        entry.phase = ""
        entry.files_done = ok_count
        if not result.any_ok and result.note:
            entry.detail = result.note  # surface why nothing landed (e.g. a single-archive torrent)
        return result, book_ids

    async def rd_download(
        self, torrent_id: str, *, name: str | None = None,
        file_ids: list[int] | None = None,
        progress: Callable[[str, int, int, str], None] | None = None,
        dest_dir: Path | None = None,
        mode: AcquireMode | None = None,
    ) -> tuple[AcquireResult, list[str]]:
        """Download a torrent (optionally only `file_ids`), then ingest the folder,
        tracked in the registry. Returns the download result and the ids of any newly
        registered books. `name` is the display label for the Downloads section
        (defaults to the torrent id); `file_ids=None` keeps the default audio+cover
        set; `progress(phase, done, total, filename)` is the per-file/resolve callback;
        `dest_dir` overrides the download location. `mode` overrides the session-sticky
        acquire mode for this download only; None falls back to `self.acquire_mode`."""
        return await self._run_download(
            torrent_id, name or torrent_id, file_ids=file_ids, progress=progress,
            dest_dir=dest_dir, mode=mode if mode is not None else self.acquire_mode,
        )

    async def resume_download(self, key: str) -> tuple[AcquireResult, list[str]]:
        """Re-run a tracked download (resume a paused one, or retry a partial/failed one) into
        its retained folder. Uses ADD mode so files already on disk are skipped and only the
        missing/incomplete ones are (re)fetched, re-applying the file subset it was started with.
        The RD cache makes re-resolving already-fetched links free, and links that previously
        failed to resolve (throttled) are re-tried."""
        entry = self._downloads.get(key)
        name = entry.name if entry else key
        file_ids = entry.file_ids if entry else None
        return await self._run_download(key, name, file_ids=file_ids, mode=AcquireMode.ADD)

    def _rd_download_dir_in_scan_paths(self) -> bool:
        return self._rd_download_dir() in self.ctx.config.scan_paths

    def should_prompt_downloads_scan(self) -> bool:
        """Whether to offer adding the downloads dir to the scan paths: only when it
        isn't already a scan path and the prompt hasn't been dismissed before."""
        return not self.ctx.config.downloads_scan_prompt_seen and not self._rd_download_dir_in_scan_paths()

    def add_downloads_to_scan_paths(self) -> None:
        """Add the downloads dir to the scan paths (deduped), dismiss the prompt, and persist."""
        cfg = self.ctx.config
        d = self._rd_download_dir()
        if d not in cfg.scan_paths:
            cfg.scan_paths = [*cfg.scan_paths, d]
        cfg.downloads_scan_prompt_seen = True
        self.save_settings(cfg)

    def mark_downloads_scan_prompt_seen(self) -> None:
        """Dismiss the downloads-scan prompt (without adding the dir) and persist."""
        cfg = self.ctx.config
        cfg.downloads_scan_prompt_seen = True
        self.save_settings(cfg)

    # --- filename parsing (interactive, FR-1.x) ---
    def book_filename(self, book: BookUnit) -> str:
        """The name used for template parsing: the first source file's filename,
        falling back to the source folder's name when no files are recorded."""
        if book.source_files:
            return book.source_files[0].path.name
        return book.source_folder.name

    def embedded_tags(self, book: BookUnit) -> EmbeddedTags | None:
        """The raw tags embedded in the book's first readable source file, so the UI can show
        what the files actually carry alongside what we detected. Returns None when the book has
        no source files on disk (identity then comes wholly from the folder/filename)."""
        from colophon.adapters.audio import read_audio_metadata

        for sf in book.source_files:
            if sf.path.exists():
                try:
                    return read_audio_metadata(sf.path)[1]
                except (OSError, ValueError) as exc:
                    logger.warning(f"could not read embedded tags from {sf.path}: {exc}")
                    return None
        return None

    def preview_filename_parse(self, book: BookUnit, template: str) -> dict[str, str]:
        """Parse `book`'s filename with `template`. Raises ValueError when the
        template is malformed; returns {} when the filename does not match."""
        pattern = compile_template(template)
        return parse_filename(pattern, self.book_filename(book)) or {}

    def filename_parse_updates(
        self, book: BookUnit, template: str, fields: set[str]
    ) -> dict[str, str]:
        """The field updates applying `template` to `book` would actually make,
        limited to `fields`: parsed, non-empty values, with `sequence` dropped
        unless the book will have a series (set_field no-ops a sequence with no
        series name). Ordered so series precedes sequence. The single source of
        truth shared by the parse preview and apply, so they cannot drift."""
        parsed = self.preview_filename_parse(book, template)
        updates = {k: v for k, v in parsed.items() if k in fields and v}
        if "sequence" in updates and "series" not in updates and not book.series:
            del updates["sequence"]
        return dict(sorted(updates.items(), key=lambda kv: kv[0] != "series"))

    def apply_filename_parse(
        self, books: list[BookUnit], template: str, fields: set[str]
    ) -> int:
        """Parse each book's filename with `template` and write the chosen `fields`
        under "filename" provenance. Each book's change is its own undoable batch.
        Returns the number of books that received at least one real field change.
        Raises ValueError on a malformed template (validated before any writes)."""
        compile_template(template)  # validate up front; raises on bad placeholders
        written: list[BookUnit] = []
        for book in books:
            updates = self.filename_parse_updates(book, template, fields)
            if not updates:
                continue
            apply_fields(
                self.ctx.books, self.ctx.history, book, updates,
                provenance=Provenance.FILENAME.value,
            )
            written.append(book)
        if written:
            self._resync_books(written)
        return len(written)

    def _push_history(self, items: list, value: object) -> None:
        """Move `value` to the front (dedup), cap the list, and persist the config."""
        if value in items:
            items.remove(value)
        items.insert(0, value)
        del items[PATTERN_HISTORY_CAP:]
        save_config(self.ctx.config, self.ctx.config_path)

    def _remove_history(self, items: list, value: object) -> None:
        if value in items:
            items.remove(value)
            save_config(self.ctx.config, self.ctx.config_path)

    def record_filename_template(self, template: str) -> None:
        """Record a used filename template at the front of the capped history."""
        t = template.strip()
        if t:
            self._push_history(self.ctx.config.recent_filename_templates, t)

    def record_directory_scheme(self, scheme: str) -> None:
        """Record a used directory scheme at the front of the capped history."""
        s = scheme.strip()
        if s:
            self._push_history(self.ctx.config.recent_directory_schemes, s)

    def record_organize_pattern(self, folder: str, file: str) -> None:
        """Record a used organize folder+file pair at the front of the capped history."""
        folder, file = folder.strip(), file.strip()
        if folder or file:
            self._push_history(
                self.ctx.config.recent_organize_patterns, OrganizePattern(folder=folder, file=file)
            )

    def remove_filename_template(self, template: str) -> None:
        self._remove_history(self.ctx.config.recent_filename_templates, template)

    def remove_directory_scheme(self, scheme: str) -> None:
        self._remove_history(self.ctx.config.recent_directory_schemes, scheme)

    def remove_organize_pattern(self, folder: str, file: str) -> None:
        self._remove_history(
            self.ctx.config.recent_organize_patterns, OrganizePattern(folder=folder, file=file)
        )

    def clear_pattern_history(self) -> None:
        """Empty all three pattern histories and persist."""
        self.ctx.config.recent_filename_templates.clear()
        self.ctx.config.recent_directory_schemes.clear()
        self.ctx.config.recent_organize_patterns.clear()
        save_config(self.ctx.config, self.ctx.config_path)

    def move_file(self, book: BookUnit, path: Path, delta: int) -> None:
        """Move a file up (delta=-1) or down (delta=+1) in the book's order."""
        paths = [sf.path for sf in book.source_files]
        if path not in paths:
            return
        i = paths.index(path)
        j = max(0, min(len(paths) - 1, i + delta))
        if i == j:
            return
        paths[i], paths[j] = paths[j], paths[i]
        file_ops.reorder(book, paths)
        book.touch()
        self.ctx.books.upsert(book)

    def exclude_file(self, book: BookUnit, path: Path) -> None:
        """Remove a file from the book's source list (does not delete it from disk)."""
        file_ops.exclude(book, path)
        book.touch()
        self.ctx.books.upsert(book)

    def rename_file(self, book: BookUnit, path: Path, new_name: str) -> Path | None:
        """Rename a file on disk; returns the new path, or None on collision/error."""
        try:
            new = file_ops.rename(book, path, new_name)
        except (OSError, ValueError) as e:
            logger.warning(f"rename failed for {path}: {e}")
            return None
        book.touch()
        self.ctx.books.upsert(book)
        return new

    _SEVERITY_RANK: ClassVar[dict[FindingSeverity, int]] = {
        FindingSeverity.ERROR: 0,
        FindingSeverity.WARN: 1,
        FindingSeverity.INFO: 2,
    }

    def _active_findings(self, book: BookUnit) -> list[Finding]:
        """Findings not dismissed via acknowledge, excluding the ones retired from the user-facing
        surface (e.g. LOOSE_IN_AUTHOR — the normal loose-file-in-author layout)."""
        return [
            f for f in book.findings
            if f.code not in book.acknowledged_findings and f.code not in SUPPRESSED_FINDINGS
        ]

    def books_needing_attention(self) -> list[BookUnit]:
        """All books carrying at least one un-acknowledged finding, most severe first."""
        flagged = [b for b in self.ctx.books.list_all() if self._active_findings(b)]
        return sorted(
            flagged,
            key=lambda b: min(self._SEVERITY_RANK[f.severity] for f in self._active_findings(b)),
        )

    def acknowledge_finding(self, book: BookUnit, code: FindingCode) -> None:
        """Dismiss an advisory finding so a re-scan won't resurface it."""
        if code not in book.acknowledged_findings:
            book.acknowledged_findings = [*book.acknowledged_findings, code]
            book.touch()
            self.ctx.books.upsert(book)

    def tag_plan(self, book: BookUnit) -> TagPlan:
        """The dry-run preview of writing this book's metadata into its files."""
        return plan_tag(self._canonical_book(book))

    async def write_tags(self, book: BookUnit) -> TagCommitResult:
        """Write tags into one book's files. See write_tags_books."""
        (result,) = await self.write_tags_books([book])
        return result

    async def write_tags_books(
        self, books: list[BookUnit],
        progress: Callable[[int, BookUnit, TagCommitResult], None] | None = None,
    ) -> list[TagCommitResult]:
        """Fetch+cache each book's cover (best effort), then write tags into its
        files on a worker thread, logging every write for recovery. All books share
        one batch id, so a single undo reverts the whole selection. `progress`, when
        given, is called after each book as (done_count, book, result) so the UI can
        show per-book status."""
        batch_id = new_batch_id()
        results: list[TagCommitResult] = []
        for book in books:
            # Backstop: never write tags into a book with a blocking error (missing/corrupt files) —
            # the write would fail. Report it as a no-op so counts stay truthful.
            if has_blocking_error(book):
                results.append(TagCommitResult(book_id=book.id))
                if progress is not None:
                    progress(len(results), book, results[-1])
                continue
            await ensure_cached_cover(book, dest_dir=book.source_folder)
            self.ctx.books.upsert(book)
            result = await asyncio.to_thread(
                commit_tag, self._canonical_book(book),
                operations=self.ctx.operations, batch_id=batch_id,
            )
            results.append(result)
            # A clean write (every file written, none failed) makes the on-disk tags
            # current, so the Tag phase is fresh. A partial/failed write leaves it as-is
            # so it doesn't read as fully tagged. Later field edits re-stale it via invalidate().
            if result.ok and result.written > 0:
                if not book.phases:
                    ensure_phases(book)
                mark(book, Phase.TAG, PhaseState.FRESH)
                resync_state(book)
                self.ctx.books.upsert(book)
            if progress is not None:
                progress(len(results), book, result)
        return results

    def undo_tag_batch(self) -> bool:
        """Revert the most recent tag batch. Returns False if there is none."""
        batch_id = self.ctx.operations.latest_batch_id()
        if batch_id is None:
            return False
        revert_tag_batch(self.ctx.operations, batch_id)
        return True

    def remap(self, book: BookUnit, *, src: str, dst: str, clear_source: bool) -> str:
        batch = remap_field(self.ctx.books, self.ctx.history, book, src=src, dst=dst, clear_source=clear_source)
        return batch

    def remap_embedded(self, book: BookUnit, *, tag: str, dst: str) -> str | None:
        """Move the book's own embedded `tag` (e.g. 'artist') into the `dst` field. Returns the undo
        batch id, or None when the file carries no such tag (nothing to move)."""
        tags = self.embedded_tags(book)
        value = embedded_value(tags, tag) if tags is not None else None
        if value is None:
            return None
        batch = set_field_value(self.ctx.books, self.ctx.history, book, dst, value)
        self.invalidate(book, Phase.TAG)
        return batch

    def bulk_remap_embedded(self, books: list[BookUnit], *, tag: str, dst: str) -> str:
        """Move each book's own embedded `tag` into `dst`, one undoable batch. Books whose file
        carries no such tag are skipped."""
        def value_for(b: BookUnit) -> str | None:
            tags = self.embedded_tags(b)
            return embedded_value(tags, tag) if tags is not None else None

        batch = bulk_remap_embedded_field(
            self.ctx.books, self.ctx.history, books, dst=dst, value_for=value_for
        )
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def bulk_edit(self, books: list[BookUnit], field: str, value: str | None) -> str:
        batch = _svc_bulk_set_field(self.ctx.books, self.ctx.history, books, field, value)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def bulk_normalize(self, books: list[BookUnit], fields: list[str]) -> str:
        """Normalize the given text `fields` across `books` in one undoable batch."""
        batch = _svc_bulk_normalize(
            self.ctx.books, self.ctx.history, books, fields, genre_policy=self.genre_policy()
        )
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def bulk_remap(self, books: list[BookUnit], *, src: str, dst: str, clear_source: bool) -> str:
        batch = _svc_bulk_remap(self.ctx.books, self.ctx.history, books, src=src, dst=dst, clear_source=clear_source)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def swap(self, book: BookUnit, *, field_a: str, field_b: str) -> str:
        return swap_fields(self.ctx.books, self.ctx.history, book, field_a, field_b)

    def bulk_swap(self, books: list[BookUnit], *, field_a: str, field_b: str) -> str:
        batch = _svc_bulk_swap(self.ctx.books, self.ctx.history, books, field_a=field_a, field_b=field_b)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def batch_changes(self, batch_id: str) -> list[EditChange]:
        """Return the recorded changes for `batch_id` (empty if none)."""
        return self.ctx.history.list_batch(batch_id)

    def undo(self, batch_id: str) -> None:
        undo_batch(self.ctx.books, self.ctx.history, batch_id)

    def undo_last(self) -> bool:
        batch_id = self.ctx.history.latest_batch_id()
        if batch_id is None:
            return False
        self.undo(batch_id)
        return True

    def mark_ready(self, book: BookUnit) -> None:
        """Mark a book Ready by human approval. A person has reviewed and accepted it, so this is a
        manual confirmation: it forces confidence to maximum rather than leaving the pre-match guess
        in place. See confirm_confidence."""
        self.confirm_confidence(book)

    def confirm_confidence(self, book: BookUnit) -> None:
        """Manually confirm a book: force confidence to 100, mark it Ready, and
        flag it as manual so the badge/recheck know it was set by hand."""
        book.confidence = 100.0
        book.confidence_signals = [
            ConfidenceSignal(name="manual_confirmation", points=100, detail="Manually confirmed")
        ]
        book.manually_confirmed = True
        mark(book, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        book.touch()
        self.ctx.books.upsert(book)

    async def recheck_confidence(self, book: BookUnit) -> None:
        """Revert to auto confidence: re-query all sources, rescore, clear the
        manual flag, and persist."""
        book = self._apply_confirmed([book])[0]
        results = await gather_matches(self.ctx.sources, query_for_book(book))
        self._rescore_after_match(book, results)
        book.touch()
        self.ctx.books.upsert(book)

    # --- match review / apply (FR-2.4, FR-3.3) ---
    def _apply_confirmed(self, books: list[BookUnit]) -> list[BookUnit]:
        """Return `books` with empty/weak author/series filled from confirmed (manual)
        folder classifications, so the workbench's confirmations reach the provider query
        without a fresh full scan. A filled book is returned as a copy (the store cache,
        which `list_all` hands out by reference, is never mutated); read-only callers
        (get_matches, identify_preview) persist nothing, while a persisting caller
        (apply_identify / recheck) saves the returned copy, per the W4b design."""
        overrides = self.ctx.overrides.all()
        if not overrides:
            return books
        return apply_confirmed_overrides(
            books, overrides, root_for=lambda b: self._scan_root_for_path(b.source_folder)
        )

    async def get_matches(self, book: BookUnit) -> list[SourceResult]:
        """Re-query all sources for `book` and return candidate matches, best first."""
        book = self._apply_confirmed([book])[0]
        results = await gather_matches(self.ctx.sources, query_for_book(book))
        return self._score(book, results).ranked

    def identify_candidates(self) -> list[BookUnit]:
        """Books eligible for Identify: not manually confirmed, not organized, with a
        title to query, and not already matched (MATCH fresh). Multi-book folders are
        split into per-work leaves at scan, so each leaf is matched on its own."""
        return [
            b for b in self.ctx.books.list_all()
            if not b.manually_confirmed
            and b.output_path is None
            and b.title
            and not b.missing
            and state_of(b, Phase.MATCH) is not PhaseState.FRESH
        ]

    async def identify_preview(
        self, books: list[BookUnit] | None = None,
        *, progress: Callable[[str, str], None] | None = None,
    ) -> IdentifyPlan:
        """Query all sources for every candidate and partition by the review threshold,
        without persisting anything. `books` scopes the match (default: identify_candidates());
        `progress(book_id, kind)` streams per-book outcomes."""
        candidates = self.identify_candidates() if books is None else books
        candidates = self._apply_confirmed(candidates)
        skipped = len(self.ctx.books.list_all()) - len(candidates)
        source_names = [s.name for s in self.ctx.sources]
        proposals = await self.quick_match_scan(candidates, source_names, progress=progress)
        return self._identify_plan(proposals, skipped)

    def _identify_plan(self, proposals: list[QuickMatchProposal], skipped: int) -> IdentifyPlan:
        """Partition scanned proposals into the IdentifyPlan counts (shared by preview/retry)."""
        threshold = self.ctx.config.review_threshold
        to_apply = sum(
            1 for p in proposals
            if p.best is not None and p.confidence >= threshold and not p.author_inferred
        )
        return IdentifyPlan(
            proposals=proposals, threshold=threshold,
            to_apply=to_apply, to_review=len(proposals) - to_apply, skipped=skipped,
        )

    async def retry_identify(
        self, plan: IdentifyPlan, book_ids: list[str],
        *, progress: Callable[[str, str], None] | None = None,
    ) -> IdentifyPlan:
        """Re-query the books in `book_ids`, replace their proposals in `plan`, and recompute
        the partition. Books not in `book_ids` keep their existing proposals."""
        wanted = set(book_ids)
        targets = [p.book for p in plan.proposals if p.book.id in wanted]
        targets = self._apply_confirmed(targets)
        source_names = [s.name for s in self.ctx.sources]
        fresh = await self.quick_match_scan(targets, source_names, progress=progress)
        fresh_by_id = {p.book.id: p for p in fresh}
        merged = [fresh_by_id.get(p.book.id, p) for p in plan.proposals]
        return self._identify_plan(merged, plan.skipped)

    def apply_identify(self, plan: IdentifyPlan) -> IdentifySummary:
        """Fill-empty apply the confident proposals (Ready) and re-score the rest
        (Needs review), in one undo batch. Manually-confirmed and organized books
        are not in the plan and are never touched."""
        items: list[tuple[BookUnit, dict[str, str | None], str]] = []
        for p in plan.proposals:
            if p.best is not None and p.confidence >= plan.threshold and not p.author_inferred:
                unchecked = self.unchecked_match_fields(p.best)
                updates = {
                    k: v for k, v in self.match_field_values(p.best).items()
                    if not get_field(p.book, k) and k not in unchecked
                }
                self._normalize_match_updates(updates)
                self._capture_match_signals(p.book, p.best, fill_empty=True)
                items.append((p.book, updates, p.best.provider))
        batch = bulk_apply_fields(self.ctx.books, self.ctx.history, items) if items else ""

        auto = 0
        for p in plan.proposals:
            ready = self._rescore_and_persist(p)
            if ready and p.best is not None and p.confidence >= plan.threshold:
                auto += 1
        if items:
            self._resync_books([book for book, _updates, _provider in items])
        return IdentifySummary(
            auto_matched=auto, routed_to_review=len(plan.proposals) - auto, batch_id=batch,
        )

    async def quick_match_scan(
        self,
        books: list[BookUnit],
        source_names: list[str],
        search_fields: set[str] | None = None,
        *,
        progress: Callable[[str, str], None] | None = None,
    ) -> list[QuickMatchProposal]:
        """For each book, query the chosen sources, score the candidates, and
        return a proposal carrying the best result, all gathered results (for
        later re-scoring), and the scan confidence. Books are scanned concurrently.
        `search_fields` (when given) restricts which fields seed the query.
        `progress(book_id, kind)` fires once per book as it resolves, kind 'ok' when a
        source returned a candidate else 'fail'."""
        chosen = [s for s in self.ctx.sources if s.name in source_names]
        sem = asyncio.Semaphore(_MATCH_CONCURRENCY)

        async def _scan(book: BookUnit) -> QuickMatchProposal:
            async with sem:
                results = await gather_matches(chosen, query_for_book(book, search_fields))
                outcome = self._score(book, results)
                if progress is not None:
                    progress(book.id, "ok" if outcome.best is not None else "fail")
                return QuickMatchProposal(
                    book=book, best=outcome.best, results=results,
                    confidence=outcome.confidence, author_inferred=outcome.author_inferred,
                )

        return list(await asyncio.gather(*(_scan(b) for b in books)))

    def quick_match_apply(self, proposals: list[QuickMatchProposal]) -> QuickMatchSummary:
        """Apply the best result of each proposal (overwrite all present fields,
        capture cover) in one undoable batch, then re-score each updated book with
        its carried results and set confidence/state. Proposals without a best
        result are skipped. Returns a summary (applied, now_ready, batch_id)."""
        applicable = [p for p in proposals if p.best is not None]
        if not applicable:
            return QuickMatchSummary()

        items: list[tuple[BookUnit, dict[str, str | None], str]] = []
        for p in applicable:
            updates = self.match_field_values(p.best)
            for field in self.unchecked_match_fields(p.best):  # don't auto-pull unchecked edition fields
                updates.pop(field, None)
            self._merge_genre_tag_updates(p.book, p.best, updates)
            self._normalize_match_updates(updates)
            self._capture_match_signals(p.book, p.best, fill_empty=False)
            items.append((p.book, updates, p.best.provider))

        batch = bulk_apply_fields(self.ctx.books, self.ctx.history, items)

        now_ready = sum(self._rescore_and_persist(p) for p in applicable)

        self._resync_books([p.book for p in applicable])

        return QuickMatchSummary(
            applied_count=len(applicable), now_ready_count=now_ready, batch_id=batch
        )

    def _capture_match_signals(self, book: BookUnit, best: SourceResult, *, fill_empty: bool) -> None:
        """Capture the non-field match signals (cover URL, abridged) onto `book`.
        With `fill_empty`, only set a value the book is missing (Identify's
        non-destructive semantics); otherwise overwrite (Quick Match)."""
        if best.cover_url and (not fill_empty or (not book.cover_path and not book.cover_url)):
            book.cover_url = best.cover_url
        if best.abridged is not None and (not fill_empty or book.abridged is None):
            book.abridged = best.abridged

    def _rescore_and_persist(self, proposal: QuickMatchProposal) -> bool:
        """Re-score a proposal's book against its carried results, then persist the
        book and sync its datafile sidecar. Returns whether the book is now Ready."""
        ready = self._rescore_after_match(
            proposal.book, proposal.results, author_inferred=proposal.author_inferred
        )
        proposal.book.touch()
        self.ctx.books.upsert(proposal.book)
        return ready

    def _rescore_after_match(
        self, book: BookUnit, results: list[SourceResult], *, author_inferred: bool = False
    ) -> bool:
        """Re-score `book` against `results`, set its confidence/signals, and mark the
        MATCH phase fresh (an online-source match). Returns True if it is now Ready.
        An `author_inferred` match never auto-readies (it routes to review). Confidence/
        state are persisted by the caller (not part of the undoable batch)."""
        outcome = self._score(book, results)
        book.confidence = outcome.confidence
        book.confidence_signals = outcome.signals
        book.manually_confirmed = False
        has_identity = bool(book.authors) or bool(book.series)
        ready = (
            outcome.confidence >= self.ctx.config.review_threshold
            and has_identity and not author_inferred
        )
        mark(book, Phase.MATCH, PhaseState.FRESH)
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        return ready

    def _authority_map(self) -> dict[str, int]:
        """provider name -> authority rank (0 = most authoritative), from the
        enabled sources in their arranged order."""
        return {s.name: i for i, s in enumerate(self.ctx.sources)}

    def _score(self, book: BookUnit, results: list[SourceResult]) -> IdentificationOutcome:
        return score_identification(book, results, authority=self._authority_map())

    def available_sources(self) -> list[tuple[str, str]]:
        """The configured metadata sources as (name, display label), in priority
        order, so the search dialog can list exactly the available services."""
        return [(s.name, _label_for(s)) for s in self.ctx.sources]

    def source_settings(self) -> list[tuple[str, str, bool]]:
        """(name, label, enabled) for every currently-known source: enabled ones in
        authority order first, then known-but-disabled providers. Re-discovers
        abs-agg so newly-available providers appear and stale ones drop."""
        enabled_names = {s.name for s in self.ctx.sources}
        rows = [(s.name, _label_for(s), True) for s in self.ctx.sources]
        rows += [
            (s.name, _label_for(s), False)
            for s in build_all_sources(self.ctx.config)
            if s.name not in enabled_names
        ]
        return rows

    def source_label(self, name: str) -> str:
        """Human-facing label for a provenance/source name. A local provenance tier
        (tag/datafile/directory/filename/graphing/manual) gets a fixed label; a match
        source (audnexus/…) gets its live source label."""
        local = provenance_label(name)
        if local is not None:
            return local
        for s in self.ctx.sources:
            if s.name == name:
                return _label_for(s)
        return _SOURCE_LABELS.get(name, name.replace("_", " ").title())

    def graph_roots(self) -> list[Path]:
        """The configured scan paths, for the graph-view root selector."""
        return list(self.ctx.config.scan_paths)

    def graph_for(
        self, root: Path, *, fresh: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> Graph:
        """Build (without persisting) the entity graph for one scan root and run the
        directory classification + author inheritance, caching by (root, fresh) so the
        diagnostic view need not rebuild on every visit. `fresh` ignores persisted book
        state (builds from disk-derived identity only). `progress(done, total, label)` fires
        per folder. Blocking — call via asyncio.to_thread / graph_for_streamed."""
        graph = build_graph(
            self.ctx.books, root,
            template=self.ctx.config.filename_template,
            directory_scheme=self.ctx.config.directory_scheme,
            fresh=fresh, progress=progress,
            single_book_folders=self.ctx.grouping.single_folders(),
        )
        classify_graph(graph, root=root)
        classify_nodes(graph, [bn.book for bn in graph.books.values()], root=root,
                       overrides=self.ctx.overrides.all(),
                       known_franchises=self.ctx.franchises.active(),
                       directory_scheme=self.ctx.config.directory_scheme)
        self._graph_cache[(str(root), fresh)] = graph
        return graph

    def classic_tree_graph(self, root: Path) -> Graph:
        """The classification tree as a view of the maintained `library_graph`: reconstructed and
        RE-CLASSIFIED in memory (no disk walk), memoized by the graph + book generations. Re-deriving
        on read (rather than restoring the persisted classification) means the tree always reflects
        the current best classification, never a stale scan-time snapshot. Classify runs on book
        COPIES so its fill_down never mutates the stored books. Rebuild / From scratch remains the
        deliberate reconcile-with-disk path."""
        key = (str(root), self.ctx.library_graph.generation, self.ctx.books.generation)
        if self._classic_graph_cache is not None and self._classic_graph_cache[0] == key:
            return self._classic_graph_cache[1]
        r = str(root)
        lib = self.ctx.library_graph
        nodes = [n for n in lib.nodes.values() if n.root == r]
        edges = [e for e in lib.edges if e.root == r]
        books_by_id = {b.id: b.model_copy(deep=True) for b in self.ctx.books.list_all()}
        graph = graph_from_records(nodes, edges, books_by_id, root=root)
        classify_graph(graph, root=root)
        classify_nodes(graph, [bn.book for bn in graph.books.values()], root=root,
                       overrides=self.ctx.overrides.all(),
                       known_franchises=self.ctx.franchises.active(),
                       directory_scheme=self.ctx.config.directory_scheme)
        self._classic_graph_cache = (key, graph)
        return graph

    def set_node_classification(self, path: Path, kind: str, value: str | None = None) -> None:
        """Persist a manual classification for the folder `path` and invalidate the graph
        cache so the next /graph load rebuilds with it applied."""
        self.ctx.overrides.set(str(path), kind, value)
        self._graph_cache.clear()
        self._resync_roots({self._scan_root_for_path(path)})

    def clear_node_classification(self, path: Path) -> None:
        """Remove the manual classification for `path` (revert to auto) and invalidate cache."""
        self.ctx.overrides.clear(str(path))
        self._graph_cache.clear()
        self._resync_roots({self._scan_root_for_path(path)})

    def folder_classification(self, path: Path) -> str:
        """The maintained graph's current kind for the directory `path` (e.g. 'author', 'title'),
        or '' when the folder has no node. Used to show a book's folder classification before a
        reclassify."""
        node = self.ctx.library_graph.nodes.get(DirectoryNode.id_for(path))
        return str(node.attrs.get("kind", "")) if node is not None else ""

    def directory_node(self, node_id: str) -> tuple[Path, str] | None:
        """Resolve a graph node id to its (path, kind) when it is a directory node — the only kind
        that is classifiable. Returns None for book/file/entity nodes (nothing to reclassify)."""
        node = self.ctx.library_graph.nodes.get(node_id)
        if node is None or node.physical != "directory":
            return None
        return Path(str(node.attrs["path"])), str(node.attrs.get("kind", ""))

    def folder_books(self, folder: Path) -> list[BookUnit]:
        """Every stored book whose files live directly in `folder` (a folder over-split into
        several books has more than one)."""
        return [b for b in self._hydrate(self.ctx.books.list_all()) if b.source_folder == folder]

    def combine_folder(self, folder: Path) -> BookUnit:
        """Combine all of `folder`'s books into one multi-file book (files become ordered
        chapters), persisting a grouping override so it survives a rescan. Returns the merged
        book."""
        merged = _svc_combine(self.ctx.books, self.ctx.grouping, folder, self.folder_books(folder))
        self._graph_cache.clear()
        self.invalidate(merged, Phase.IDENTIFY)  # re-derive fields/chapters over the new file set
        self._resync_roots({self._scan_root_for_path(folder)})
        return self._hydrate([self.ctx.books.get(merged.id)])[0]

    def uncombine_folder(self, folder: Path) -> list[BookUnit]:
        """Reverse a combine on `folder`: clear the grouping override and restore the separate
        books from the snapshot. Returns the restored books."""
        restored = _svc_uncombine(self.ctx.books, self.ctx.grouping, folder)
        self._graph_cache.clear()
        self._resync_roots({self._scan_root_for_path(folder)})
        return restored

    def folder_is_combined(self, folder: Path) -> bool:
        """Whether `folder` has a 'single book' grouping override (from a Combine)."""
        return self.ctx.grouping.is_single(str(folder))

    def set_entity_alias(self, kind: str, source_name: str, canonical_name: str) -> None:
        """Merge or rename an author/series/franchise entity: alias `source_name` to
        `canonical_name`. Merge = canonical is an existing entity; rename = canonical is
        a new display name. Honored live by the navigator; book fields are never rewritten."""
        self.ctx.aliases.set(kind, _name_key(source_name), canonical_name)

    def clear_entity_alias(self, kind: str, source_name: str) -> None:
        """Remove an entity alias (revert `source_name` to its auto-derived entity)."""
        self.ctx.aliases.clear(kind, _name_key(source_name))

    def _canonical_book(self, book: BookUnit) -> BookUnit:
        """The book as the graph names it: author/series names resolved to their
        canonical entity names (merge/rename overrides). Non-destructive — for
        projecting to disk, never persisted."""
        return canonical_book(book, self.ctx.aliases.all())

    def confirm_hint_cohort(self, root: Path, hint: str) -> int:
        """Confirm every grouping under `root` hinted `hint` (author/series) as that kind,
        each with its folder name as the value. Excludes the root. Returns the count."""
        graph = self.graph_for(root)
        nodes = grouping_cohort(graph, root=root, hint=hint)
        self.ctx.overrides.set_many([(str(n.path), hint, n.path.name) for n in nodes])
        self._graph_cache.clear()
        self._resync_roots({root})
        return len(nodes)

    def list_franchises(self) -> list[str]:
        """User-declared franchise display names, sorted case-insensitively (the removable
        entries in Manage -> Franchises). Built-in seeds are listed separately; see
        `builtin_franchises`."""
        return sorted(self.ctx.franchises.all().values(), key=str.casefold)

    def builtin_franchises(self) -> list[str]:
        """The always-on, built-in franchise names, sorted case-insensitively. Shown read-only
        in Manage -> Franchises so a user can see what is recognized without declaring it."""
        from colophon.core.franchise_seeds import DEFAULT_FRANCHISE_NAMES
        return sorted(DEFAULT_FRANCHISE_NAMES, key=str.casefold)

    def known_franchises(self) -> list[str]:
        """Franchise names for autocomplete: declared + built-in + any already assigned to a
        book. Sorted case-insensitively."""
        names = set(self.list_franchises()) | set(self.builtin_franchises())
        names |= {b.franchise for b in self.ctx.books.list_all() if b.franchise}
        return sorted(names, key=str.casefold)

    def add_franchise(self, name: str) -> None:
        """Declare a franchise; invalidate the graph cache so the next build reclassifies."""
        name = name.strip()
        if not name:
            return
        self.ctx.franchises.add(name)
        self._graph_cache.clear()

    def remove_franchise(self, name: str) -> None:
        """Undeclare a franchise; invalidate the graph cache so the next build reclassifies."""
        self.ctx.franchises.remove(name)
        self._graph_cache.clear()

    def cached_graph(self, root: Path, *, fresh: bool = False) -> Graph | None:
        """The previously-built graph for `(root, fresh)`, or None if not built this
        session. A snapshot — not invalidated by scans/edits; Rebuild refreshes it."""
        return self._graph_cache.get((str(root), fresh))

    async def graph_for_streamed(
        self, root: Path, *, fresh: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> Graph:
        """Run graph_for off the event loop, marshaling per-folder progress back onto it so
        a live UI indicator updates safely from the worker thread."""
        loop = asyncio.get_running_loop()

        def safe(done: int, total: int, label: str) -> None:
            if progress is not None:
                loop.call_soon_threadsafe(progress, done, total, label)

        return await asyncio.to_thread(self.graph_for, root, fresh=fresh, progress=safe)

    def source_tooltip(self, name: str) -> str:
        """Hover explanation for a provenance badge: a fixed sentence for a local tier,
        or 'Matched from <source>' for an external match source."""
        local = provenance_tooltip(name)
        if local is not None:
            return local
        return f"Matched from {self.source_label(name)}"

    def review_threshold(self) -> float:
        """The confidence threshold above which a match is auto-checked / a book is Ready."""
        return self.ctx.config.review_threshold

    async def search_matches(
        self,
        book: BookUnit,
        *,
        title: str | None,
        author: str | None,
        series: str | None,
        asin: str | None,
        isbn: str | None = None,
        source_name: str,
    ) -> list[SourceResult]:
        """Query a single chosen source with the (user-edited) fields and return
        the results ranked against `book`. An unknown source name or a source
        error yields an empty list (logged)."""
        source = next((s for s in self.ctx.sources if s.name == source_name), None)
        if source is None:
            logger.warning(f"search_matches: unknown source {source_name!r}")
            return []
        query = SourceQuery(
            title=(title or "").strip() or None,
            author=(author or "").strip() or None,
            series=(series or "").strip() or None,
            asin=(asin or "").strip() or None,
            isbn=(isbn or "").strip() or None,
        )
        try:
            results = await source.search(query)
        except Exception as e:  # a source failing must not crash the search (BLE001 intentional)
            logger.warning(f"source {source_name} failed in search_matches: {e}")
            return []
        # The browse list leads with the closest-runtime edition (Audible returns per-edition
        # candidates); score still orders equally-close runtimes and any runtime-less results.
        return sort_by_runtime_closeness(book, self._score(book, results).ranked)

    @staticmethod
    def match_field_values(result: SourceResult) -> dict[str, str | None]:
        """Map a source result's present fields to editable-field updates. The
        single source of truth for which fields a match offers (the UI picker and
        apply both consume this), so the two cannot drift."""
        updates: dict[str, str | None] = {}
        if result.title:
            updates["title"] = result.title
        if result.subtitle:
            updates["subtitle"] = result.subtitle
        if result.authors:
            updates["author"] = "; ".join(result.authors)
        if result.narrators:
            updates["narrator"] = "; ".join(result.narrators)
        if result.series_name:
            updates["series"] = result.series_name
        if result.series_sequence is not None:
            updates["sequence"] = str(result.series_sequence)
        if result.publish_year is not None:
            updates["year"] = str(result.publish_year)
        # Only take an ASIN from an audiobook source: a physical/Kindle ASIN (e.g. from Hardcover)
        # is the wrong product for an audiobook and would dead-end the later Audible/Audnexus lookup.
        if result.asin and result.provider in AUDIOBOOK_PROVIDERS:
            updates["asin"] = result.asin
        if result.isbn:
            updates["isbn"] = result.isbn
        if result.publisher:
            updates["publisher"] = result.publisher
        if result.language:
            updates["language"] = result.language
        if result.description:
            updates["description"] = result.description
        if result.genres:
            updates["genre"] = "; ".join(result.genres)
        if result.tags:
            updates["tag"] = "; ".join(result.tags)
        return updates

    def unchecked_match_fields(self, result: SourceResult) -> set[str]:
        """Fields the match picker should offer but leave UNCHECKED by default (and auto-apply
        should skip): edition-specific fields (publisher, ISBN) from a non-audiobook source, per
        `config.strict_source_fields`. Empty when the setting is off or the source is audiobook."""
        return unchecked_edition_fields(
            result.provider, self.match_field_values(result).keys(),
            strict=self.ctx.config.strict_source_fields,
        )

    def _merge_genre_tag_updates(
        self, book: BookUnit, result: SourceResult, updates: dict[str, str | None]
    ) -> None:
        """Rewrite any genre/tag entries in `updates` to merge with the book's
        existing genres/tags (union, deduped) so applying a match never clobbers
        curated entries. Mutates `updates` in place. Genres dedupe
        case-insensitively (normalize_genres); tags dedupe exactly, order-preserving
        (merge_preserve)."""
        if "genre" in updates:
            incoming = self.genre_policy().canonicalize(result.genres)
            updates["genre"] = "; ".join(normalize_genres(book.genres + incoming)) or None
        if "tag" in updates:
            updates["tag"] = "; ".join(merge_preserve(book.tags, result.tags)) or None

    def _normalize_match_updates(self, updates: dict[str, str | None]) -> None:
        """Run the configured auto-normalize fields (config.normalize_on_match)
        through FIELD_NORMALIZERS in place. No-op for fields not selected, absent,
        None, or without a normalizer."""
        for field in self.ctx.config.normalize_on_match:
            normalizer = FIELD_NORMALIZERS.get(field)
            value = updates.get(field)
            if normalizer is not None and value:
                updates[field] = normalizer(value)

    def apply_match_fields(self, book: BookUnit, result: SourceResult, fields: set[str]) -> str:
        """Apply only the chosen fields from `result` (per-field selection), stamping
        the source as provenance. Returns the batch id of the editable-field changes
        (undoable). The pseudo-field "cover" captures result.cover_url onto the book
        (fetched/embedded later); that capture is persisted but is NOT part of the
        undoable batch."""
        if "cover" in fields and result.cover_url:
            book.cover_url = result.cover_url
        if result.abridged is not None:
            book.abridged = result.abridged
        updates = {k: v for k, v in self.match_field_values(result).items() if k in fields}
        self._merge_genre_tag_updates(book, result, updates)
        self._normalize_match_updates(updates)
        batch = apply_fields(self.ctx.books, self.ctx.history, book, updates, provenance=result.provider)
        # Re-score against the applied result so the book's confidence and state
        # reflect the match, consistent with Quick Match. This records the MATCH phase.
        self._rescore_after_match(book, [result])
        book.touch()
        self.ctx.books.upsert(book)
        self.invalidate(book, Phase.TAG)
        self._resync_books([book])
        return batch

    def apply_match(self, book: BookUnit, result: SourceResult) -> str:
        """Apply all present fields from a chosen source result (and its cover)."""
        fields = set(self.match_field_values(result))
        if result.cover_url:
            fields.add("cover")
        return self.apply_match_fields(book, result, fields)

    # --- chapters ---
    async def apply_audnexus_chapters(
        self, book: BookUnit, asin: str | None = None
    ) -> ChapterApplyResult:
        """Fetch named chapters from Audnexus for `asin` (or book.asin), store them
        on the book, and report a runtime mismatch vs the source files."""
        target = (asin or book.asin or "").strip()
        if not target:
            return ChapterApplyResult(ok=False, error="no ASIN")
        source = next((s for s in self.ctx.sources if s.name == "audnexus"), None)
        fetch_chapters = getattr(source, "fetch_chapters", None)
        if fetch_chapters is None:
            return ChapterApplyResult(ok=False, error="Audible source not available")
        fetch = await fetch_chapters(target)
        if fetch is None or not fetch.chapters:
            return ChapterApplyResult(ok=False, error="no chapters found")
        book.chapters = fetch.chapters
        book.touch()
        self.ctx.books.upsert(book)
        source_runtime_ms = book.duration_ms
        return ChapterApplyResult(
            ok=True,
            count=len(fetch.chapters),
            audible_runtime_ms=fetch.runtime_ms,
            source_runtime_ms=source_runtime_ms,
            mismatch=runtime_mismatch(source_runtime_ms, fetch.runtime_ms),
        )

    def reset_chapters(self, book: BookUnit) -> None:
        """Clear stored chapters (revert to the file-boundary default)."""
        book.chapters = []
        book.touch()
        self.ctx.books.upsert(book)

    def save_chapters(self, book: BookUnit, chapters: list[Chapter]) -> None:
        """Persist hand-edited chapters, sorting by start and recomputing the ends
        against the book runtime so the stored timeline stays consistent."""
        book.chapters = normalize_chapters(chapters, book.duration_ms)
        book.touch()
        self.ctx.books.upsert(book)

    # --- encode + organize ---
    def ready_books(self) -> list[BookUnit]:
        return self.ctx.books.list_by_state(BookState.READY)

    def pipeline_counts(self) -> dict[str, int]:
        """Cheap per-stage readiness counts for the header stepper: books locally identified and
        awaiting a match, and books Ready to persist. From the indexed state column, no hydration."""
        by_state = self.ctx.books.count_by_state()
        return {
            "identified": by_state.get(BookState.IDENTIFIED.value, 0),
            "ready": by_state.get(BookState.READY.value, 0),
        }

    def scope_counts(self, *, ready_state: BookState = BookState.READY) -> dict[str, int]:
        """Cheap ready-tier and total counts for the scope selector, from the indexed state column
        (no hydration). `ready_state` selects which state the 'ready' chip counts: READY for
        Persist, IDENTIFIED for Match, so the toggle label always agrees with the set that scope
        resolves to."""
        by_state = self.ctx.books.count_by_state()
        return {"ready": by_state.get(ready_state.value, 0), "total": sum(by_state.values())}

    @timed("books_for_scope")
    def books_for_scope(
        self, scope: str, selected_ids: set[str] | None = None,
        *, ready_state: BookState = BookState.READY,
    ) -> list[BookUnit]:
        """Resolve a Match/Persist scope to a concrete book list (hydrated). 'selected' = the given
        ids; 'ready' = books in `ready_state` (READY for Persist, IDENTIFIED for Match); anything
        else ('all') = the whole library. The ready scope reads the same stored `state` column as
        the header counts, so the header count and the resolved set always agree."""
        if scope == "ready":
            return self._hydrate(self.ctx.books.list_by_state(ready_state))
        if scope == "selected":
            stored = (self.ctx.books.get(i) for i in (selected_ids or set()))
            return self._hydrate([b for b in stored if b is not None])
        return self._hydrate(self.ctx.books.list_all())

    def _encode_target(self, book: BookUnit) -> Path:
        """In-place output path for an encode: <source_folder>/<sanitized title>.m4b
        (falls back to the book id when there's no usable title)."""
        stem = sanitize_name(book.title or book.id) or book.id
        return book.source_folder / f"{stem}.m4b"

    def organize_targets(
        self, books: list[BookUnit], *, patterns: PathPatterns | None = None
    ) -> list[tuple[str, Path]]:
        """Pure dry-run: the (book_id, target_path) each book would organize to, computed
        from `patterns` (or the saved patterns). Encodes/moves nothing."""
        pats = patterns or self.ctx.patterns
        root = self.ctx.config.library_root or (default_db_path().parent / "library")
        return [(b.id, build_target_path(root, pats, self._canonical_book(b))) for b in books]

    def duplicate_destinations(
        self, *, patterns: PathPatterns | None = None
    ) -> list[DuplicateDestination]:
        """Library books that would organize to the same destination under the saved settings —
        the persist dry-run (`organize_targets`) over the whole library, grouped by colliding
        target path so a human can resolve the clash before organizing. In-library only: it
        compares previewed targets, not what already exists on disk."""
        # A titleless book has no real target (it collapses to a degenerate "…/.m4b") and would never
        # be persisted, so it can't be a genuine "when persisted" clash — exclude it from the compare.
        books = [b for b in self._hydrate(self.ctx.books.list_all()) if (b.title or "").strip()]
        by_id = {b.id: b for b in books}
        return [
            DuplicateDestination(
                target=path,
                books=[
                    CollidingBook(bid, by_id[bid].title or "(untitled)", by_id[bid].source_folder)
                    for bid in ids
                ],
            )
            for path, ids in duplicate_targets(self.organize_targets(books, patterns=patterns))
        ]

    def organize_preview(
        self, books: list[BookUnit], *, patterns: PathPatterns | None = None, encode: bool = True
    ) -> list[OrganizePreviewRow]:
        """Dry-run rows for the Persist preview: each book's organize destination, whether it
        collides with existing content, and whether a blocking error will skip it. Writes
        nothing. When `encode` (the common path), the destination is the produced M4B file and
        a collision is that file already existing. Without encode, a reorg copies the original
        files into the book folder (one or many), so the destination shown is that folder and a
        collision is a folder that already holds content; exact per-file collisions are still
        caught at organize time."""
        targets = dict(self.organize_targets(books, patterns=patterns))
        rows: list[OrganizePreviewRow] = []
        for b in books:
            target = targets[b.id]
            if encode:
                dest, collision = target, target.exists()
            else:
                folder = target.parent
                dest, collision = folder, (folder.exists() and any(folder.iterdir()))
            rows.append(
                OrganizePreviewRow(
                    book_id=b.id,
                    title=b.title or "(untitled)",
                    target=dest,
                    collision=collision,
                    blocked=has_blocking_error(b),
                )
            )
        return rows

    def _process_book(self, book: BookUnit, options: EncodeJobOptions) -> BookProcessResult:
        """Persist one book, guaranteeing a readable reason on any failure. Wraps the worker so an
        unexpected error (permissions, disk, a bad path, a tag-write blow-up) is caught and recorded
        as a FAILED phase with its message — surfaced on At a Glance — instead of escaping the run
        and leaving the persist with no explanation."""
        try:
            return self._persist_book(book, options)
        except Exception as exc:  # persist must always report a reason, never crash the run
            logger.exception(f"persist failed for book {book.id} [{book.title!r}]")
            # Fail whichever phase was mid-flight (encode marks ENCODE running), else the organize
            # umbrella, so the failed step and its reason show up on the book.
            phase = Phase.ENCODE if state_of(book, Phase.ENCODE) is PhaseState.RUNNING else Phase.ORGANIZE
            return self._fail_persist(book, phase, f"{type(exc).__name__}: {exc}")

    def _fail_persist(self, book: BookUnit, phase: Phase, reason: str) -> BookProcessResult:
        """Record a persist failure: mark `phase` FAILED with `reason`, persist it (so the failed
        step + its reason show on At a Glance), and return the matching failed result."""
        mark(book, phase, PhaseState.FAILED, detail=reason)
        resync_state(book)
        book.touch()
        self.ctx.books.upsert(book)
        return BookProcessResult(book_id=book.id, status="failed", detail=reason)

    def _persist_book(self, book: BookUnit, options: EncodeJobOptions) -> BookProcessResult:
        """Run the selected operations for one book: encode (in place, untagged) ->
        organize (move) -> tag once at the resting path -> optional source delete."""
        # Backstop: a book with a blocking error (missing/corrupt files) can't be persisted — no
        # in-app edit fixes it and attempting would error. Skip it even if a stale UI let it through.
        if has_blocking_error(book):
            return BookProcessResult(book_id=book.id, status="skipped", detail="blocking error")

        if not options.encode:
            # No-encode reorg: copy/rename the original source files into the library,
            # naming multi-part books one file per part. No transcoding.
            if not book.source_files:
                return BookProcessResult(book_id=book.id, status="skipped", detail="no source files")
            if not options.organize:
                return BookProcessResult(book_id=book.id, status="done")
            library_root = self.ctx.config.library_root or (default_db_path().parent / "library")
            cbook = self._canonical_book(book)
            tracks = [read_embedded_tags(sf.path).track for sf in book.source_files]
            ordered = resolve_part_order(book.source_files, tracks)
            if ordered is None:
                reason = (f"couldn't order {len(book.source_files)} part(s) — track numbers are "
                          "missing or duplicated, so the file order is ambiguous")
                return self._fail_persist(book, Phase.ORGANIZE, reason)
            targets = build_reorg_targets(
                library_root, options.patterns or self.ctx.patterns, cbook, ordered
            )
            pairs = list(zip([sf.path for sf in ordered], targets, strict=True))
            org = organize_book_parts(
                self.ctx.books, book, pairs,
                delete_sources=options.delete_sources or self.ctx.config.reorg_delete_sources,
            )
            if not org.moved or org.target_path is None:
                return self._fail_persist(book, Phase.ORGANIZE, _organize_fail_detail(org))
            total = len(ordered)
            batch_id = new_batch_id()
            for idx, dst in enumerate(targets, start=1):
                self.ctx.operations.record(OperationRecord(
                    batch_id=batch_id, book_id=book.id, op_type=_OP_ORGANIZE,
                    target=str(dst), before=None, outcome="ok",
                ))
                if not tag_file(
                    dst, cbook, operations=self.ctx.operations, batch_id=batch_id,
                    track=(idx if total > 1 else None),
                ):
                    logger.warning(f"track tag write failed for {dst} (book {book.id})")
            return BookProcessResult(book_id=book.id, status="done")

        # Encode path: transcode all source files into a single output, then organize + tag.
        target = self._encode_target(book)
        if target.exists() and target != book.output_path:
            return self._fail_persist(
                book, Phase.ENCODE, f"an encoded file already exists at the output path: {target}"
            )
        mark(book, Phase.ENCODE, PhaseState.RUNNING)
        resync_state(book)
        self.ctx.books.upsert(book)
        enc = encode_book(
            book, target, bitrate=self.ctx.config.transcode_bitrate,
            delete_sources=options.delete_sources, confirm_delete=options.delete_sources,
            chapters=book.chapters or None,
        )
        if not enc.verified or enc.output_path is None:
            return self._fail_persist(book, Phase.ENCODE, f"encode failed: {enc.error or 'unknown error'}")
        book.output_path = enc.output_path
        mark(book, Phase.ENCODE, PhaseState.FRESH)
        resync_state(book)
        book.touch()
        self.ctx.books.upsert(book)

        if options.organize:
            library_root = self.ctx.config.library_root or (default_db_path().parent / "library")
            cbook = self._canonical_book(book)
            target = build_target_path(
                library_root, options.patterns or self.ctx.patterns, cbook
            )
            org = organize_book(self.ctx.books, book, book.output_path, target=target)
            if not org.moved or org.target_path is None:
                return self._fail_persist(book, Phase.ORGANIZE, _organize_fail_detail(org))

        batch_id = new_batch_id()
        resting = book.output_path
        self.ctx.operations.record(OperationRecord(
            batch_id=batch_id, book_id=book.id, op_type=_OP_ORGANIZE,
            target=str(resting), before=None, outcome="ok",
        ))
        tag_file(
            resting, self._canonical_book(book),
            operations=self.ctx.operations, batch_id=batch_id,
        )
        return BookProcessResult(book_id=book.id, status="done")

    async def run_encode_job(
        self,
        books: list[BookUnit],
        options: EncodeJobOptions,
        *,
        progress: Callable[[str, str], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> EncodeJobResult:
        """Run the selected encode/organize/delete operations across `books` with
        bounded concurrency. Graceful cancel: a book not yet started after the token
        is set is reported 'cancelled'; in-flight books finish. `progress(book_id,
        status)` is called as each book moves through its steps."""
        sem = asyncio.Semaphore(max(1, options.concurrency))
        _terminal = {"encoded", "organized", "failed", "cancelled", "skipped"}

        with self.ctx.jobs.track("Encode + organize" if options.encode else "Organize") as job:
            counted = {"n": 0}
            job.progress(0, len(books), "")

            def _emit(book_id: str, status: str) -> None:
                if status in _terminal:
                    counted["n"] += 1
                    job.progress(counted["n"], len(books), status)
                if progress is not None:
                    progress(book_id, status)

            async def _one(book: BookUnit) -> BookProcessResult:
                if cancel is not None and cancel.cancelled:
                    _emit(book.id, "cancelled")
                    return BookProcessResult(book_id=book.id, status="cancelled")
                async with sem:
                    if cancel is not None and cancel.cancelled:
                        _emit(book.id, "cancelled")
                        return BookProcessResult(book_id=book.id, status="cancelled")
                    _emit(book.id, "encoding" if options.encode else "organizing")
                    if options.encode:
                        await self.ensure_cover_cached(book)
                    result = await asyncio.to_thread(self._process_book, book, options)
                    _emit(book.id, result.status)
                    return result

            results = await asyncio.gather(*(_one(b) for b in books))
            return EncodeJobResult(results=list(results))

    def process_one(self, book: BookUnit, *, confirm_delete: bool = False) -> ProcessResult:
        """Encode + organize a single book (delegates to the unified worker, which
        handles encode-in-place, single-tag, and optional source delete)."""
        res = self._process_book(
            book, EncodeJobOptions(encode=True, organize=True, delete_sources=confirm_delete),
        )
        organized = res.status == "done" and book.state == BookState.ORGANIZED
        return ProcessResult(
            book_id=book.id,
            encoded=book.state in (BookState.ENCODED, BookState.ORGANIZED),
            organized=organized,
            detail=res.detail,
        )

    def process_ready(
        self,
        *,
        confirm_delete: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[ProcessResult]:
        ready = self.ready_books()
        total = len(ready)
        results: list[ProcessResult] = []
        for i, book in enumerate(ready, start=1):
            results.append(self.process_one(book, confirm_delete=confirm_delete))
            if progress is not None:
                progress(i, total, book.title or book.id)
        return results

    # --- external integrations (FR-7) ---
    async def trigger_abs_scan(self) -> bool:
        """Trigger an AudiobookShelf library scan. Returns False if ABS isn't
        configured or the scan failed (graceful degradation, FR-7.4)."""
        client = self.ctx.abs_client
        library_id = self.ctx.config.audiobookshelf_library_id
        if client is None or not library_id:
            return False
        try:
            await client.scan_library(library_id)
            return True
        except Exception as e:  # never let an integration failure crash the caller
            logger.warning(f"ABS scan failed: {e}")
            return False

    @staticmethod
    def import_ll_patterns(config_ini: Path) -> str:
        """Read the folder organize pattern from a LazyLibrarian config.ini, for
        the Settings importer. Returns the folder pattern; raises FileNotFoundError
        when the path does not exist. File/multi-part naming is Colophon's own and
        is not imported."""
        if not config_ini.exists():
            raise FileNotFoundError(config_ini)
        return read_audiobook_patterns(config_ini).folder
