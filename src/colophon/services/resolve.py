"""Scoped re-evaluate service (Slice 1: book scope).

Re-read one book's files and re-derive it in frozen folder context, returning a result the caller
persists. Composes the proven local-phase refresher; it does not rewrite the scan core and it
persists nothing itself. The file-change diff is a pure function so it is trivially testable."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo
from colophon.adapters.scan import group_book_units
from colophon.core.classify import corrupt_source_files
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import BookUnit, SourceFile
from colophon.core.phases import LOCAL
from colophon.core.reassociate import is_missing
from colophon.services.ingest import ScanPlan, run_local_phases


@dataclass
class FileChanges:
    """What changed on disk for a scope since it was last read."""
    added: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    renamed: list[tuple[Path, Path]] = field(default_factory=list)  # (old, new), matched by (size, dur)
    corrupt_resolved: list[Path] = field(default_factory=list)      # was corrupt/empty, now readable
    newly_corrupt: list[Path] = field(default_factory=list)         # was readable, now corrupt/empty
    missing_resolved: list[str] = field(default_factory=list)       # book ids: was missing, now present
    newly_missing: list[str] = field(default_factory=list)          # book ids: was present, now missing

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.renamed or self.corrupt_resolved
                    or self.newly_corrupt or self.missing_resolved or self.newly_missing)

    def summary(self) -> str:
        parts: list[str] = []
        if self.added:
            parts.append(f"{len(self.added)} file(s) added")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        if self.renamed:
            parts.append(f"{len(self.renamed)} renamed")
        if self.corrupt_resolved:
            parts.append(f"{len(self.corrupt_resolved)} corrupt file(s) resolved")
        if self.newly_corrupt:
            parts.append(f"{len(self.newly_corrupt)} now corrupt")
        if self.missing_resolved:
            parts.append("no longer missing")
        if self.newly_missing:
            parts.append("now missing")
        return ", ".join(parts) if parts else "No changes on disk"


@dataclass
class ResolveResult:
    """Outcome of a scoped re-evaluate: a plan the caller persists, plus the disk-diff to surface."""
    plan: ScanPlan
    changes: FileChanges


def _corrupt(sf: SourceFile) -> bool:
    """A file with real size but no readable audio — the exact EMPTY_AUDIO rule."""
    return bool(corrupt_source_files([sf]))


def compute_file_changes(
    prior: list[SourceFile], post: list[SourceFile], *,
    prior_missing: bool, post_missing: bool, book_id: str,
) -> FileChanges:
    """Diff a book's file set before/after a re-probe. Pure — no disk access."""
    prior_by = {sf.path: sf for sf in prior}
    post_by = {sf.path: sf for sf in post}
    added = [p for p in post_by if p not in prior_by]
    removed = [p for p in prior_by if p not in post_by]
    corrupt_resolved = [p for p in post_by if p in prior_by and _corrupt(prior_by[p]) and not _corrupt(post_by[p])]
    newly_corrupt = [p for p in post_by if p in prior_by and not _corrupt(prior_by[p]) and _corrupt(post_by[p])]
    renamed: list[tuple[Path, Path]] = []
    for old in list(removed):
        ps = prior_by[old]
        match = next(
            (np for np in added
             if post_by[np].size == ps.size and post_by[np].duration_seconds == ps.duration_seconds),
            None,
        )
        if match is not None:
            renamed.append((old, match))
            removed.remove(old)
            added.remove(match)
    return FileChanges(
        added=added, removed=removed, renamed=renamed,
        corrupt_resolved=corrupt_resolved, newly_corrupt=newly_corrupt,
        missing_resolved=[book_id] if prior_missing and not post_missing else [],
        newly_missing=[book_id] if post_missing and not prior_missing else [],
    )


def resolve_scope(
    repo: BookUnitRepo,
    book: BookUnit,
    *,
    root: Path,
    template: str,
    directory_scheme: str = "",
) -> ResolveResult:
    """Book scope: re-read `book`'s folder and re-derive the book alone in frozen folder context —
    siblings and the file->book partition never move — returning a ResolveResult the caller commits.

    In a single-book folder the book owns the whole folder, so new/removed/renamed files are picked
    up. In a multi-book folder the book re-probes only its currently-owned files (the partition is
    frozen); an unowned new file is left for a folder-scope re-cluster (a later slice). The caller
    decides whether to clear weak identity first (a from-scratch re-derive) — this service runs the
    local phases as given."""
    folder = book.source_folder
    prior = list(book.source_files)
    prior_missing = book.missing

    on_disk = next((u.files for u in group_book_units(folder) if u.folder == folder), [])
    multi_book = len(repo.ids_in_folder(folder)) > 1
    if multi_book:
        owned = {sf.path.name for sf in prior}
        unit_files = [p for p in on_disk if p.name in owned]
    else:
        unit_files = list(on_disk)

    run_local_phases(
        book, frozenset(LOCAL), force=True,
        root=root, pattern=compile_template(template), scheme=parse_scheme(directory_scheme),
        unit_files=unit_files,
    )

    changes = compute_file_changes(
        prior, list(book.source_files),
        prior_missing=prior_missing,
        # Match the codebase's unmount guard: a whole root going offline must not read as the
        # book's files vanishing (a false "now missing"). Derive it from disk, don't pin True.
        post_missing=is_missing(book, root_accessible=root.exists()),
        book_id=book.id,
    )
    return ResolveResult(plan=ScanPlan(units=[book]), changes=changes)
