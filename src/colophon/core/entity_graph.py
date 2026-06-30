"""A live, in-memory semantic entity graph built from the current books: author /
series / franchise nodes (deduped by normalized name) with membership edges to the
books. Pure, rebuilt each render — the navigator's source for the author / series /
franchise views. NOT the persisted graph store (which is scan-time and stale on edits)."""

from __future__ import annotations

from dataclasses import dataclass, field

from colophon.core.entity_alias import resolve_alias
from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookUnit, _Base

EntityKey = tuple[str, str]  # (kind, name_key)


class EntityNode(_Base):
    kind: str   # "author" | "series" | "franchise"
    name: str   # canonical display name — first spelling encountered
    key: str    # _name_key(name) — identity within a kind


@dataclass
class EntityGraph:
    """Entity nodes + membership edges over a set of live books. `members` is the
    forward edge (entity -> its books, in encounter order); `book_entities` is the
    reverse adjacency (book id -> the entity keys it links to)."""

    nodes: dict[EntityKey, EntityNode] = field(default_factory=dict)
    members: dict[EntityKey, list[BookUnit]] = field(default_factory=dict)
    book_entities: dict[str, list[EntityKey]] = field(default_factory=dict)
    books: list[BookUnit] = field(default_factory=list)


def build_entity_graph(
    books: list[BookUnit],
    *,
    franchise_of: dict[str, str] | None = None,
    aliases: dict[tuple[str, str], str] | None = None,
) -> EntityGraph:
    """Build the entity graph from live books. Author/series names come from the book's
    fields, franchise from `franchise_of[book.id]`; all are resolved through the entity
    overrides (`aliases`) and deduped by `_name_key`. A name repeated on one book links
    once. Holds only real entity nodes (the legacy pseudo-author is a view concern)."""
    franchise_of = franchise_of or {}
    g = EntityGraph(books=list(books))

    def link(kind: str, raw_name: str, book: BookUnit, seen: set[EntityKey]) -> None:
        name = resolve_alias(aliases, kind, raw_name)
        ek = (kind, _name_key(name))
        if ek in seen:  # a name repeated on one book links once
            return
        seen.add(ek)
        g.nodes.setdefault(ek, EntityNode(kind=kind, name=name, key=ek[1]))
        g.members.setdefault(ek, []).append(book)
        g.book_entities.setdefault(book.id, []).append(ek)

    for b in books:
        seen: set[EntityKey] = set()
        for a in b.authors:
            link("author", a, b, seen)
        for s in b.series:
            link("series", s.name, b, seen)
        raw_f = franchise_of.get(b.id)
        if raw_f:
            link("franchise", raw_f, b, seen)

    return g
