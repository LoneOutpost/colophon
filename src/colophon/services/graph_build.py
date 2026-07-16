"""Build the entity graph from a scan, and project it back to BookUnits (Phase 1).

`build_graph` runs the existing `plan_scan` (no writes) and wraps each resulting
BookUnit in the node structure; `project` reconstructs source_folder/source_files
from the structural nodes and returns the books. Round-trips to `plan_scan` output.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph, leaf_id_for
from colophon.core.models import (
    BookUnit,
    ContentKind,
    DetectedWork,
    Provenance,
    SeriesRef,
)
from colophon.services.ingest import ScanOptions, plan_scan

# Author provenances read from a single representative FILE (the container's first). Not inherited
# by fanned-out leaves: each leaf must read its OWN file's tags. Folder-level tiers (directory/
# filename) do apply to every work and are inherited.
_FILE_TAG_PROV = frozenset({Provenance.TAG.value, Provenance.DATAFILE.value})


def _leaf_book(container: BookUnit, work: DetectedWork, leaf_id: str) -> BookUnit:
    """Project one detected work into a SINGLE-book leaf BookUnit. Title/author/series
    come from the work (FILENAME provenance); `source_files` is left for `project` to
    reconstruct from the leaf's owned FileNodes. Folder-name author inheritance for
    untagged works is Phase 3 (GRAPHING), so an untagged work's leaf has no authors."""
    leaf = BookUnit.new(source_folder=container.source_folder)
    leaf.id = leaf_id
    leaf.content_kind = ContentKind.SINGLE
    leaf.title = work.label
    leaf.provenance["title"] = Provenance.FILENAME.value
    leaf.detected_works = [work]
    if work.author:                       # the work named its own author
        leaf.authors = [work.author]
        leaf.provenance["authors"] = Provenance.FILENAME.value
    elif container.authors and container.provenance.get("authors") not in _FILE_TAG_PROV:
        # Inherit only a FOLDER-level author (directory/filename) that applies to every work here.
        # A tag/datafile author was read from the container's FIRST file alone, so it names that one
        # book, not its siblings — inheriting it would mask each leaf's own embedded artist. Leave
        # such leaves authorless for per-file IDENTIFY (reads the leaf's own tags) + GRAPHING to fill.
        leaf.authors = list(container.authors)
        leaf.provenance["authors"] = container.provenance.get(
            "authors", Provenance.DIRECTORY.value
        )
    if work.series:
        leaf.series = [SeriesRef(name=work.series, sequence=work.sequence)]
        leaf.provenance["series"] = Provenance.FILENAME.value
    return leaf


def _leaves_for(book: BookUnit) -> list[tuple[BookUnit, list[Path]]]:
    """The logical books a container yields, with the file paths each owns. More than one
    detected work fans out into one leaf per work — multiple works are multiple books, even
    when the folder's content-kind confidence stayed UNKNOWN (e.g. two same-title files that
    are separate editions, not chapters). A single work owns all the folder's files (a genuine
    multi-file book keeps its chapters together)."""
    works = book.detected_works
    if len(works) > 1:
        return [
            (_leaf_book(book, w, leaf_id_for(book.source_folder, w.files)), list(w.files))
            for w in works
        ]
    return [(book, [sf.path for sf in book.source_files])]


def _ensure_ancestors(g: Graph, folder: Path, root: Path) -> None:
    """Materialize folder's ancestor DirectoryNodes up to (and including) root, linking
    each child dir into its parent's child_dirs. Idempotent; stops at root."""
    child = folder
    while child != root and root in child.parents:
        parent = child.parent
        pnode = g.directories.setdefault(
            DirectoryNode.id_for(parent), DirectoryNode(path=parent)
        )
        cid = DirectoryNode.id_for(child)
        if cid not in pnode.child_dirs:
            pnode.child_dirs.append(cid)
        child = parent


def build_graph(
    repo: BookUnitRepo, root: Path, *, template: str, directory_scheme: str = "",
    options: ScanOptions | None = None, inference_root: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None, fresh: bool = False,
) -> Graph:
    """Run a (non-persisting) scan and wrap each BookUnit in Directory/File/Book nodes."""
    plan = plan_scan(
        repo, root, template=template, directory_scheme=directory_scheme,
        options=options, inference_root=inference_root, progress=progress, fresh=fresh,
    )
    g = Graph()
    for book in plan.units:
        d = g.directories.setdefault(
            DirectoryNode.id_for(book.source_folder),
            DirectoryNode(path=book.source_folder),
        )
        _ensure_ancestors(g, book.source_folder, root)
        file_id_by_path: dict[Path, str] = {}
        for sf in book.source_files:
            fn = FileNode(
                path=sf.path, role=FileRole.AUDIO, source_file=sf, raw_stem=sf.path.stem
            )
            g.files[fn.id] = fn
            file_id_by_path[sf.path] = fn.id
            if fn.id not in d.child_files:
                d.child_files.append(fn.id)
        for leaf, owned_paths in _leaves_for(book):
            owned = [file_id_by_path[p] for p in owned_paths if p in file_id_by_path]
            bn = BookNode(id=leaf.id, book=leaf, owns=owned, dir_id=d.id)
            g.books[bn.id] = bn
            if bn.id not in d.books:
                d.books.append(bn.id)
    return g


def project(graph: Graph) -> list[BookUnit]:
    """Reconstruct each Book node's BookUnit, taking source_folder from its
    DirectoryNode and source_files from its owned FileNodes (the structural layer is
    the source for those). Phase 1: the other fields ride on the embedded BookUnit."""
    out: list[BookUnit] = []
    for bn in graph.books.values():
        book = bn.book
        book.source_folder = graph.directories[bn.dir_id].path
        book.source_files = [
            graph.files[fid].source_file
            for fid in bn.owns
            if graph.files[fid].source_file is not None
        ]
        out.append(book)
    return out
