"""What the Library's Details pane should be showing.

The rule is small — two or more books selected is a bulk edit, one is that book, none is the empty
state, and an explicitly opened book outranks all of it — but it used to be implemented
independently in five places inside `render_workspace` (show_detail, show_bulk, repaint,
_after_select, and the page's opening block). Three of the five agreed. Both selection bugs found in
September came from the two that did not, and neither was testable without rendering the whole page.

So the rule lives here: pure, one owner, no NiceGUI. The workspace holds one `DetailPane`, hands it
the live selection set, and asks it what to render instead of each site deciding for itself.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal

PaneKind = Literal["empty", "single", "bulk"]


@dataclass(frozen=True)
class Pane:
    """One of the three states the Details pane can be in.

    `book_id` is set only for "single" — the book whose editor is on screen.
    """

    kind: PaneKind
    book_id: str | None = None

    @property
    def is_bulk(self) -> bool:
        return self.kind == "bulk"


class DetailPane:
    """Owns which pane the Details column should show.

    Built with the workspace's live `selected_ids` set — read on every question, never copied, so an
    in-place mutation from any of the page's selection controls is reflected without a hand-off.

    Two inputs decide the answer. An explicit open (a row click, a `?open=` deep link, an action on
    one book) means the user asked for that book, so it outranks the selection and holds until the
    selection itself moves. Otherwise the selection decides.
    """

    def __init__(self, selected: AbstractSet[str]) -> None:
        self._selected = selected
        self._opened: str | None = None

    def open(self, book_id: str | None) -> Pane:
        """Explicitly show one book, or pass None to drop back to what the selection implies."""
        self._opened = book_id or None
        return self.current()

    def selection_changed(self) -> Pane:
        """The selection moved, so an earlier explicit open no longer speaks for the user."""
        self._opened = None
        return self.current()

    def current(self) -> Pane:
        """What should be on screen right now. Asks nothing to change."""
        if self._opened:
            return Pane("single", self._opened)
        if len(self._selected) >= 2:
            return Pane("bulk")
        if len(self._selected) == 1:
            return Pane("single", next(iter(self._selected)))
        return Pane("empty")
