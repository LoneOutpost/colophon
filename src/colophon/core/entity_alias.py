"""Entity-override resolution: map a raw observed entity name to the canonical
name the graph holds (from the merge/rename overrides in `entity_aliases`), and
project a book through that mapping. Pure — no I/O. Shared by the live navigator
and the apply-side (organize/tagging) canonical projection."""

from __future__ import annotations

from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookUnit


def resolve_alias(
    aliases: dict[tuple[str, str], str] | None, kind: str, name: str
) -> str:
    """Map an entity name to its canonical name, following alias chains (`A->B->C`)
    with a self/cycle guard so it always terminates. `kind` is author/series/franchise;
    keys are `(kind, _name_key(source))`. Returns `name` unchanged when there's no alias."""
    if not aliases:
        return name
    seen: set[str] = set()
    cur = name
    while True:
        ck = _name_key(cur)
        nxt = aliases.get((kind, ck))
        if nxt is None or ck in seen:
            break
        seen.add(ck)
        if _name_key(nxt) == ck:
            # Same-key rename (e.g. a pure casing fix): adopt the display, then stop
            # so we don't re-resolve the identical key forever.
            cur = nxt
            break
        cur = nxt
    return cur


def canonical_book(book: BookUnit, overrides: dict[tuple[str, str], str]) -> BookUnit:
    """A non-destructive view of `book` with its author and series names resolved to
    the canonical names the graph holds. New `authors` list and freshly-copied
    `SeriesRef`s (sequence preserved); every other field is shared by reference but
    never mutated by a projection. Returns `book` unchanged when there are no overrides."""
    if not overrides:
        return book
    authors = [resolve_alias(overrides, "author", a) for a in book.authors]
    series = [
        s.model_copy(update={"name": resolve_alias(overrides, "series", s.name)})
        for s in book.series
    ]
    return book.model_copy(update={"authors": authors, "series": series})
