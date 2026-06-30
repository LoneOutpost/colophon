"""A live, in-memory semantic entity graph built from the current books: author /
series / franchise nodes (deduped by normalized name) with membership edges to the
books. Pure, rebuilt each render — the navigator's source for the author / series /
franchise views. NOT the persisted graph store (which is scan-time and stale on edits)."""

from __future__ import annotations

from dataclasses import dataclass, field

from colophon.core.entity_alias import resolve_alias
from colophon.core.graph_resolve import _name_key
from colophon.core.library_graph import LibraryGraph
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


def entity_graph_from_records(
    library_graph: LibraryGraph,
    books_by_id: dict[str, BookUnit],
    *,
    aliases: dict[tuple[str, str], str] | None = None,
) -> EntityGraph:
    """Build the navigator's EntityGraph from the maintained persisted graph instead of
    live book fields: author/series/franchise entity nodes + book->entity edges, raw names
    resolved through aliases at read time, book metadata joined via book_id. Same shape as
    build_entity_graph, so the view builders consume it unchanged. A book node whose
    book_id has no BookUnit is skipped (defensive)."""
    g = EntityGraph()
    entity_name: dict[str, tuple[str, str]] = {}   # entity node id -> (kind, raw name)
    book_of_node: dict[str, str] = {}              # book node id -> book_id
    for nid, n in library_graph.nodes.items():
        if n.semantic in ("author", "series", "franchise"):
            name = n.attrs.get("name")
            if isinstance(name, str):
                entity_name[nid] = (n.semantic, name)
        elif n.semantic == "book":
            bid = n.attrs.get("book_id")
            if isinstance(bid, str):
                book_of_node[nid] = bid

    seen: dict[str, set[EntityKey]] = {}  # per-book-per-kind dedup, matching build_entity_graph
    for e in library_graph.edges:
        if e.kind not in ("author", "series", "franchise"):
            continue
        bid = book_of_node.get(e.src)
        ent = entity_name.get(e.dst)
        if bid is None or ent is None:
            continue
        book = books_by_id.get(bid)
        if book is None:
            continue  # graph book node with no BookUnit
        kind, raw_name = ent
        name = resolve_alias(aliases, kind, raw_name)
        ek = (kind, _name_key(name))
        bseen = seen.setdefault(bid, set())
        if ek in bseen:
            continue
        bseen.add(ek)
        g.nodes.setdefault(ek, EntityNode(kind=kind, name=name, key=ek[1]))
        g.members.setdefault(ek, []).append(book)
        g.book_entities.setdefault(bid, []).append(ek)

    # `g.books` carries the joined BookUnits so the navigator's view builders can read
    # per-book fields (the series-but-no-author pseudo-author reads `b.series`). This
    # relies on the invariant that a book node's edges mirror the book's current fields —
    # held by write-through resyncing every mutation (see AppController._resync_books).
    g.books = [books_by_id[bid] for bid in book_of_node.values() if bid in books_by_id]
    return g
