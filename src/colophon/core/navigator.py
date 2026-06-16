"""Read models for the workspace navigator: library tree and directory listings."""

from __future__ import annotations

from pathlib import Path

from colophon.core.models import BookUnit, _Base


class SeriesNode(_Base):
    name: str
    books: list[BookUnit] = []


class AuthorNode(_Base):
    name: str
    series: list[SeriesNode] = []
    standalone: list[BookUnit] = []


class LibraryTree(_Base):
    needs_id: list[BookUnit] = []
    authors: list[AuthorNode] = []


class DirEntry(_Base):
    path: Path
    name: str
    is_dir: bool
    is_audio: bool


class DirectoryListing(_Base):
    path: Path
    entries: list[DirEntry] = []
