"""Read models for the workspace navigator: library tree and directory listings."""

from __future__ import annotations

from pathlib import Path

from colophon.core.entity_alias import resolve_alias
from colophon.core.entity_graph import EntityGraph, build_entity_graph
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
    series: list[SeriesNode] = []   # noqa: RUF012 - series-node-rooted view
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
    """Assemble the library tree by traversing a live entity graph: author/series/
    franchise nodes (deduped by `_name_key`) with membership edges to books. Each book
    is reachable from every author and series it has (multi-membership). The author view
    nests series under authors; the series view is rooted at series nodes; both honor
    entity overrides. A book with neither author nor series is `needs_id`; a book with a
    series but no author keeps its legacy pseudo-author home (first series name)."""
    g = build_entity_graph(books, franchise_of=franchise_of, aliases=aliases)
    needs_id = sorted(
        (b for b in books if not b.authors and not b.series), key=lambda b: b.confidence
    )
    return LibraryTree(
        needs_id=needs_id,
        authors=_author_view(g, aliases),
        series=_series_view(g, aliases),
        franchises=_franchise_view(g),
        all_books=list(books),
    )


def _author_view(
    g: EntityGraph, aliases: dict[tuple[str, str], str] | None
) -> list[AuthorNode]:
    """Author roots from the graph's author nodes, plus the legacy pseudo-author home
    (keyed by the first series name) for series-but-no-author books. Each author's books
    are nested by series and sorted by sequence; standalone books by title."""
    author_books: dict[str, list[BookUnit]] = {}
    author_display: dict[str, str] = {}
    for (kind, key), node in g.nodes.items():
        if kind == "author":
            author_display[key] = node.name
            author_books[key] = list(g.members[(kind, key)])
    for b in g.books:
        if not b.authors and b.series:
            pname = resolve_alias(aliases, "author", b.series[0].name)
            pk = _name_key(pname)
            author_display.setdefault(pk, pname)
            author_books.setdefault(pk, []).append(b)

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
    return authors


def _series_view(
    g: EntityGraph, aliases: dict[tuple[str, str], str] | None
) -> list[SeriesNode]:
    """Series roots from the graph's series nodes; each node's books across all authors,
    sorted by the book's sequence within that series (resolving the book's raw series
    name through overrides to match the node)."""
    nodes = [n for (kind, _), n in g.nodes.items() if kind == "series"]
    return [
        SeriesNode(
            name=n.name,
            books=sorted(
                g.members[("series", n.key)],
                key=lambda b, k=n.key: max(
                    (
                        _series_sequence(b, _name_key(s.name))
                        for s in b.series
                        if _name_key(resolve_alias(aliases, "series", s.name)) == k
                    ),
                    default=0.0,
                ),
            ),
        )
        for n in sorted(nodes, key=lambda n: n.name.casefold())
    ]


def _franchise_view(g: EntityGraph) -> list[FranchiseNode]:
    """Franchise roots from the graph's franchise nodes; books sorted by title."""
    nodes = [n for (kind, _), n in g.nodes.items() if kind == "franchise"]
    return [
        FranchiseNode(
            name=n.name,
            books=sorted(g.members[("franchise", n.key)], key=lambda b: b.title or ""),
        )
        for n in sorted(nodes, key=lambda n: n.name.casefold())
    ]


class DirEntry(_Base):
    path: Path
    name: str
    is_dir: bool
    is_audio: bool


class DirectoryListing(_Base):
    path: Path
    entries: list[DirEntry] = []  # noqa: RUF012 - pydantic field default, copied per instance
