"""Group a Real-Debrid torrent's files into a folder tree for per-file selection.

Only RD-selected files (the ones that have a downloadable link) appear. The tree
drives the Acquire per-file picker and its smart default."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from colophon.adapters.audio import is_audio_file
from colophon.adapters.realdebrid import RdTorrentFile


@dataclass
class FileEntry:
    id: int
    name: str   # basename
    path: str   # full RD path
    bytes: int
    is_audio: bool


@dataclass
class FolderNode:
    name: str   # folder path; "" for root-level files
    files: list[FileEntry] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def has_audio(self) -> bool:
        return any(f.is_audio for f in self.files)


def _folder_of(path: str) -> str:
    parent = str(PurePosixPath(path.strip("/")).parent)
    return "" if parent == "." else parent


def build_file_tree(files: list[RdTorrentFile]) -> list[FolderNode]:
    """Folder-grouped tree of the torrent's RD-selected files (sorted by folder,
    then file name). Non-selected files (no link) are omitted."""
    groups: dict[str, FolderNode] = {}
    for f in files:
        if not f.selected:
            continue
        folder = _folder_of(f.path)
        node = groups.setdefault(folder, FolderNode(name=folder))
        node.files.append(
            FileEntry(
                id=f.id, name=PurePosixPath(f.path).name, path=f.path,
                bytes=f.bytes, is_audio=is_audio_file(Path(f.path)),
            )
        )
    for node in groups.values():
        node.files.sort(key=lambda e: e.name)
    return [groups[k] for k in sorted(groups)]


def is_single_audiobook(tree: list[FolderNode]) -> bool:
    """One book: at most one folder contains audio (vs a multi-book bundle)."""
    return len([n for n in tree if n.has_audio]) <= 1


def default_selection(tree: list[FolderNode]) -> set[int]:
    """No preselection: the user picks what to download."""
    return set()
