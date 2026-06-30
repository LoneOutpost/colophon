"""The in-memory library graph: the persisted property graph (directory/file/book/
entity nodes + typed edges) materialized as records, loaded at startup. Slice 1 holds
load (from records) and a file-reference validity check; reads and write-through come
later. Book/entity nodes reference the book store by id — no metadata is copied here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from colophon.core.graph_records import EdgeRecord, NodeRecord


@dataclass
class LibraryGraph:
    nodes: dict[str, NodeRecord] = field(default_factory=dict)
    edges: list[EdgeRecord] = field(default_factory=list)

    @classmethod
    def from_records(
        cls, nodes: list[NodeRecord], edges: list[EdgeRecord]
    ) -> LibraryGraph:
        """Index nodes by id; keep edges as a list (adjacency is deferred to the reader
        slice). Last node wins on a duplicate id (ids are unique in the store)."""
        return cls(nodes={n.id: n for n in nodes}, edges=list(edges))

    def replace_root(
        self, root: str, nodes: list[NodeRecord], edges: list[EdgeRecord]
    ) -> None:
        """Replace everything under `root`: drop all in-memory nodes/edges whose `root`
        matches, then add the new set. The per-root primitive that keeps the in-memory
        graph in lockstep with the store's replace_subgraph (a root emptied by reconcile
        drops to no nodes — handled)."""
        self.nodes = {nid: n for nid, n in self.nodes.items() if n.root != root}
        self.edges = [e for e in self.edges if e.root != root]
        for n in nodes:
            self.nodes[n.id] = n
        self.edges.extend(edges)


@dataclass
class GraphValidity:
    """File/directory nodes whose paths no longer exist on disk."""

    missing_dirs: list[str] = field(default_factory=list)   # node ids
    missing_files: list[str] = field(default_factory=list)  # node ids
    missing_paths: list[str] = field(default_factory=list)  # the absent paths, for logging


def check_file_references(
    graph: LibraryGraph, *, exists: Callable[[Path], bool] = Path.exists
) -> GraphValidity:
    """Check that the graph's directory/file node paths still exist on disk, directory
    first: a file under an already-missing directory is flagged without its own probe.
    `exists` is injectable so callers/tests can avoid the real filesystem. Book/entity
    nodes (no `path`) are skipped."""
    report = GraphValidity()
    missing_dir_paths: set[str] = set()

    def present(path: str) -> bool:
        # An unprobeable path (e.g. permission denied on a mount) is treated as
        # present: we can't confirm it's gone, and a validity check must never crash
        # startup. `Path.exists` re-raises non-ENOENT OSErrors, so guard them here.
        try:
            return exists(Path(path))
        except OSError:
            return True

    for n in graph.nodes.values():
        if n.physical != "directory":
            continue
        path = n.attrs.get("path")
        if not isinstance(path, str):
            continue
        if not present(path):
            report.missing_dirs.append(n.id)
            report.missing_paths.append(path)
            missing_dir_paths.add(path)

    for n in graph.nodes.values():
        if n.physical != "file":
            continue
        path = n.attrs.get("path")
        if not isinstance(path, str):
            continue
        if str(Path(path).parent) in missing_dir_paths:
            report.missing_files.append(n.id)        # pruned: parent dir already gone
            report.missing_paths.append(path)
        elif not present(path):
            report.missing_files.append(n.id)
            report.missing_paths.append(path)

    return report
