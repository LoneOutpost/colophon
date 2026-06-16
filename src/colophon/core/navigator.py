"""Read models for the workspace navigator: library tree and directory listings."""

from __future__ import annotations

from pathlib import Path

from colophon.core.models import BookUnit, _Base


class SeriesNode(_Base):
    name: str
    books: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


class AuthorNode(_Base):
    name: str
    series: list[SeriesNode] = []  # noqa: RUF012 - pydantic field default, copied per instance
    standalone: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


class LibraryTree(_Base):
    needs_id: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance
    authors: list[AuthorNode] = []  # noqa: RUF012 - pydantic field default, copied per instance


class DirEntry(_Base):
    path: Path
    name: str
    is_dir: bool
    is_audio: bool


class DirectoryListing(_Base):
    path: Path
    entries: list[DirEntry] = []  # noqa: RUF012 - pydantic field default, copied per instance
