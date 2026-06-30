"""Read models for the workspace navigator: library tree and directory listings."""

from __future__ import annotations

from pathlib import Path

from colophon.core.entity_alias import resolve_alias
from colophon.core.graph_resolve import _name_key  # shared name normalizer, not a graph coupling
from colophon.core.models import BookUnit, _Base


class SeriesNode(_Base):
    name: str
    books: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


class AuthorNode(_Base):
    name: str
    series: list[SeriesNode] = []  # noqa: RUF012 - pydantic field default, copied per instance
    standalone: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


class FranchiseNode(_Base):
    name: str
    books: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


class LibraryTree(_Base):
    needs_id: list[BookUnit] = []   # noqa: RUF012 - pydantic field default, copied per instance
    authors: list[AuthorNode] = []  # noqa: RUF012 - pydantic field default, copied per instance
    franchises: list[FranchiseNode] = []  # noqa: RUF012 - pydantic field default, copied per instance
    all_books: list[BookUnit] = []  # noqa: RUF012 - flat, unique (multi-membership-safe)


def _series_sequence(book: BookUnit, name_key: str) -> float:
    """The book's sequence within the series whose name matches `name_key` (0.0 if unset)."""
    for s in book.series:
        if _name_key(s.name) == name_key and s.sequence is not None:
            return s.sequence
    return 0.0


def build_library_tree(
    books: list[BookUnit],
    *,
    franchise_of: dict[str, str] | None = None,
    aliases: dict[tuple[str, str], str] | None = None,
) -> LibraryTree:
    """Group books into the entity model over live books: each book under EVERY author
    (and series) it has, authors/series deduped by `_name_key`, plus a franchise tier
    (from `franchise_of`) and a flat unique `all_books`. A book with neither author nor
    series is `needs_id`. A book with a series but no author keeps its legacy home: a
    pseudo-author keyed by its first series name."""
    franchise_of = franchise_of or {}
    needs_id = sorted(
        (b for b in books if not b.authors and not b.series), key=lambda b: b.confidence
    )

    author_books: dict[str, list[BookUnit]] = {}
    author_display: dict[str, str] = {}
    for b in books:
        raw_keys = b.authors or ([b.series[0].name] if b.series else [])
        keys = [resolve_alias(aliases, "author", n) for n in raw_keys]
        seen_ak: set[str] = set()  # a name repeated on one book files it once
        for name in keys:
            k = _name_key(name)
            if k in seen_ak:
                continue
            seen_ak.add(k)
            author_display.setdefault(k, name)
            author_books.setdefault(k, []).append(b)

    authors: list[AuthorNode] = []
    for k in sorted(author_books, key=lambda k: author_display[k].casefold()):
        in_series: dict[str, list[BookUnit]] = {}
        series_display: dict[str, str] = {}
        standalone: list[BookUnit] = []
        for b in author_books[k]:
            if b.series:
                seen_sk: set[str] = set()  # a series repeated on one book lists it once
                for s in b.series:
                    s_name = resolve_alias(aliases, "series", s.name)
                    sk = _name_key(s_name)
                    if sk in seen_sk:
                        continue
                    seen_sk.add(sk)
                    series_display.setdefault(sk, s_name)
                    in_series.setdefault(sk, []).append(b)
            else:
                standalone.append(b)
        series_nodes = [
            SeriesNode(
                name=series_display[sk],
                books=sorted(
                    in_series[sk],
                    key=lambda b, sk=sk: max(
                        (
                            _series_sequence(b, _name_key(s.name))
                            for s in b.series
                            if _name_key(resolve_alias(aliases, "series", s.name)) == sk
                        ),
                        default=0.0,
                    ),
                ),
            )
            for sk in sorted(in_series, key=lambda sk: series_display[sk].casefold())
        ]
        authors.append(AuthorNode(
            name=author_display[k], series=series_nodes,
            standalone=sorted(standalone, key=lambda b: b.title or ""),
        ))

    franchise_books: dict[str, list[BookUnit]] = {}
    franchise_display: dict[str, str] = {}
    for b in books:
        raw = franchise_of.get(b.id)
        if raw:
            name = resolve_alias(aliases, "franchise", raw)
            fk = _name_key(name)
            franchise_display.setdefault(fk, name)
            franchise_books.setdefault(fk, []).append(b)
    franchises = [
        FranchiseNode(
            name=franchise_display[fk],
            books=sorted(franchise_books[fk], key=lambda b: b.title or ""),
        )
        for fk in sorted(franchise_books, key=lambda fk: franchise_display[fk].casefold())
    ]

    return LibraryTree(needs_id=needs_id, authors=authors, franchises=franchises, all_books=list(books))


class DirEntry(_Base):
    path: Path
    name: str
    is_dir: bool
    is_audio: bool


class DirectoryListing(_Base):
    path: Path
    entries: list[DirEntry] = []  # noqa: RUF012 - pydantic field default, copied per instance
