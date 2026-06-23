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


def build_library_tree(books: list[BookUnit]) -> LibraryTree:
    """Group books into Author -> Series/standalone, plus a needs-id list.

    A book with neither author nor series goes to `needs_id` (sorted by
    confidence). Otherwise it is filed under its first author (or, lacking an
    author, its first series name); within an author, series are sorted by name
    and their books by sequence, and standalone titles are sorted by title."""
    needs_id = sorted(
        (b for b in books if not b.authors and not b.series),
        key=lambda b: b.confidence,
    )
    identified = [b for b in books if b.authors or b.series]

    by_author: dict[str, list[BookUnit]] = {}
    for b in identified:
        author = b.authors[0] if b.authors else b.series[0].name
        by_author.setdefault(author, []).append(b)

    authors: list[AuthorNode] = []
    for author in sorted(by_author):
        in_series: dict[str, list[BookUnit]] = {}
        standalone: list[BookUnit] = []
        for b in by_author[author]:
            if b.series:
                in_series.setdefault(b.series[0].name, []).append(b)
            else:
                standalone.append(b)
        series_nodes = [
            SeriesNode(
                name=name,
                books=sorted(
                    items,
                    key=lambda b: (
                        b.series[0].sequence if b.series and b.series[0].sequence is not None else 0.0
                    ),
                ),
            )
            for name, items in sorted(in_series.items())
        ]
        authors.append(
            AuthorNode(
                name=author,
                series=series_nodes,
                standalone=sorted(standalone, key=lambda b: b.title or ""),
            )
        )
    return LibraryTree(needs_id=needs_id, authors=authors)


class DirEntry(_Base):
    path: Path
    name: str
    is_dir: bool
    is_audio: bool


class DirectoryListing(_Base):
    path: Path
    entries: list[DirEntry] = []  # noqa: RUF012 - pydantic field default, copied per instance
