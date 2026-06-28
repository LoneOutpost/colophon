"""Entity-graph node model (Phase 1).

Directory and File nodes form the physical/containment layer; a Book node is the
logical leaf. In Phase 1 a Book node EMBEDS the existing BookUnit — fields migrate
onto the node in later phases. Ids are path-derived (the hybrid identity: a
`stable_id` slot is reserved for later, unused now).
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from enum import StrEnum
from pathlib import Path

from colophon.core.models import BookUnit, SourceFile, _Base


def _node_id(path: Path) -> str:
    normalized = unicodedata.normalize("NFC", os.path.normpath(str(path)))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def leaf_id_for(folder: Path, files: list[Path]) -> str:
    """Stable id for a logical book owning `files` within `folder`.

    Whole-folder ownership (no files listed) keeps `_node_id(folder)`, so single
    books never change id. A subset — a multi-book leaf — gets a hash of the folder
    plus its sorted file names, so each leaf is distinct and order-independent.
    """
    names = sorted({p.name for p in files})
    if not names:
        return _node_id(folder)
    return _node_id(Path(str(folder) + "\x00" + "\x00".join(names)))


class FileRole(StrEnum):
    AUDIO = "audio"        # owned by a Book; constitutes it
    DATAFILE = "datafile"  # metadata.json — auxiliary evidence
    COVER = "cover"
    TEXT = "text"
    OTHER = "other"


class FileNode(_Base):
    path: Path
    role: FileRole
    source_file: SourceFile | None = None  # the probed audio (role AUDIO)
    raw_stem: str = ""

    @property
    def id(self) -> str:
        return _node_id(self.path)

    @staticmethod
    def id_for(path: Path) -> str:
        return _node_id(path)


class BookNode(_Base):
    id: str
    book: BookUnit            # embedded for Phase 1; fields migrate onto the node later
    owns: list[str] = []      # noqa: RUF012 - FileNode ids (audio)
    dir_id: str = ""          # the DirectoryNode it resides in
    stable_id: str | None = None  # reserved (unused v1)


class DirectoryNode(_Base):
    path: Path
    kind: str = "unknown"     # AUTHOR/SERIES/TITLE/CONTAINER classification — later phases
    author: str | None = None  # the author this node represents when kind == "author"
    child_dirs: list[str] = []   # noqa: RUF012
    child_files: list[str] = []  # noqa: RUF012
    books: list[str] = []        # noqa: RUF012 - BookNode ids residing here
    kind_confidence: float = 0.0          # 0..1 confidence in `kind`
    kind_evidence: list[str] = []          # noqa: RUF012 - human-readable reasons for `kind`
    kind_hint: str = ""                    # advisory: "author" | "series" | "ambiguous" | ""
    kind_hint_confidence: float = 0.0
    kind_hint_evidence: list[str] = []      # noqa: RUF012 - reasons for the hint
    kind_source: str = ""                  # "" = auto, "manual" = a persisted user override
    kind_value: str | None = None           # the override's value (franchise/author/series name)

    @property
    def id(self) -> str:
        return _node_id(self.path)

    @staticmethod
    def id_for(path: Path) -> str:
        return _node_id(path)


class Graph(_Base):
    """A built scan graph keyed by node id."""

    directories: dict[str, DirectoryNode] = {}  # noqa: RUF012
    files: dict[str, FileNode] = {}             # noqa: RUF012
    books: dict[str, BookNode] = {}             # noqa: RUF012
