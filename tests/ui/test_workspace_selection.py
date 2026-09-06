"""The Details pane must agree with the selection.

Two or more books selected means the bulk editor; exactly one means that book; none means the
empty state. The pane is rebuilt from several places (a restored tab snapshot, a repaint, each
selection control), and every one of them has to land on the same rule.
"""

import asyncio
from pathlib import Path

import pytest
from nicegui import core, ui
from nicegui.client import Client
from nicegui.events import (
    GenericEventArguments,
    KeyboardAction,
    KeyboardKey,
    KeyboardModifiers,
    KeyEventArguments,
    handle_event,
)
from nicegui.page import page

import colophon.ui.workspace as ws
from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, SourceFile
from colophon.core.view_state import RestoredView


@pytest.fixture
async def loop_registered():
    """NiceGUI defers event handlers until it knows the running loop; a headless test has to say."""
    previous = core.loop
    core.loop = asyncio.get_running_loop()
    yield
    core.loop = previous


@pytest.fixture
def library(tmp_path: Path):
    """A three-book library and its controller."""
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    controller = AppController(ctx)
    for i in range(3):
        src = tmp_path / "ingest" / "Author A" / f"Book {i}"
        src.mkdir(parents=True)
        (src / "a.mp3").write_bytes(b"")
        book = BookUnit.new(source_folder=src)
        book.title, book.authors = f"Book {i}", ["Author A"]
        book.source_files = [
            SourceFile(path=src / "a.mp3", size=1, duration_seconds=1.0, ext="mp3")
        ]
        ctx.books.upsert(book)
    yield controller, sorted(b.id for b in ctx.books.list_all())
    ctx.close()


class _Workspace:
    """A rendered workspace, addressed by the elements it built."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def _of(self, kind: str) -> list:
        return [e for e in self._client.elements.values() if type(e).__name__ == kind]

    def labels(self) -> list[str]:
        return [e.text for e in self._of("Label")]

    def detail_pane(self) -> str:
        """Which of the three Details-pane states is on screen."""
        labels = self.labels()
        if any(text and text.startswith("Editing ") for text in labels):
            return "bulk"
        if "Select a book" in labels:
            return "empty"
        return "single"

    def click(self, text: str) -> None:
        button = next(e for e in self._of("Button") if e.text == text)
        for listener in button._event_listeners.values():
            if listener.type == "click":
                handle_event(
                    listener.handler,
                    GenericEventArguments(sender=button, client=self._client, args={}),
                )

    def action_bar(self) -> list[str]:
        """Button labels in the fixed action bar above the Details scroll area. These act on ONE
        book (Save / Write tags / Mark ready), so a multi-selection must leave it empty."""
        bar = next(e for e in self._client.elements.values()
                   if "colophon-actionbar" in e._classes)
        return [c.text for c in bar.default_slot.children if type(c).__name__ == "Button"]

    def select_row(self, index: int) -> None:
        """Tick the leading checkbox on the index-th book row."""
        rows = [c for c in self._of("Checkbox") if not c.text]
        rows[index].set_value(True)

    def bulk_actions(self) -> list[str]:
        """Button labels inside the bulk editor (everything the Details pane offers below the bar)."""
        return [b.text for b in self._of("Button")]

    def detail_title(self) -> str | None:
        """The title in the single-book editor, i.e. which book the pane is showing."""
        for element in self._of("Input"):
            if element._props.get("label") == "title":
                return element.value
        return None

    def press(self, name: str, code: str) -> None:
        # Entered on the client because NiceGUI's own dispatch restores the slot context, and the
        # focus handler builds UI (it scrolls the focused row into view).
        keyboard = self._of("Keyboard")[0]
        with self._client:
            keyboard._key_handlers[0](KeyEventArguments(
                sender=keyboard, client=self._client,
                action=KeyboardAction(keydown=True, keyup=False, repeat=False),
                key=KeyboardKey(name=name, code=code, location=0),
                modifiers=KeyboardModifiers(alt=False, ctrl=False, meta=False, shift=False),
            ))

    def focus_next_row(self) -> None:
        self.press("ArrowDown", "ArrowDown")

    def toggle_focused_row(self) -> None:
        self.press(" ", "Space")

    async def settle(self, ticks: int = 40) -> None:
        for _ in range(ticks):
            await asyncio.sleep(0.02)


async def _render(
    controller: AppController, *, restored: RestoredView | None = None, open_book_id: str = "",
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> _Workspace:
    """Render the workspace, optionally as if a tab snapshot had just been restored."""
    if restored is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(ws, "snapshot_to_view", lambda _snap, **_kw: restored)
    client = Client(page("/"))
    with client:
        ws.render_workspace(controller, ui.dark_mode(), open_book_id=open_book_id)
    workspace = _Workspace(client)
    await workspace.settle()
    return workspace


def _restored(selected: list[str], open_book_id: str | None) -> RestoredView:
    return RestoredView(
        scope={"kind": "all", "key": None}, folder_filter_path=None,
        view={"multiselect": True, "group_by": "author"}, filter_text="",
        selected_ids=set(selected), open_book_id=open_book_id,
    )


async def test_select_all_opens_the_bulk_editor(loop_registered, library):
    controller, _ids = library
    workspace = await _render(controller)
    workspace.click("Select all")
    await workspace.settle()
    assert workspace.detail_pane() == "bulk"


async def test_restored_multi_selection_beats_the_remembered_book(
    loop_registered, library, monkeypatch,
):
    # Reloading the tab restored both the selection and the book that had been open, and the
    # remembered book won — so the pane showed one book while the footer said "3 selected".
    controller, ids = library
    workspace = await _render(
        controller, restored=_restored(ids, ids[0]), monkeypatch=monkeypatch,
    )
    assert workspace.detail_pane() == "bulk"


async def test_restored_multi_selection_with_no_remembered_book_is_not_empty(
    loop_registered, library, monkeypatch,
):
    # The other half of the same bug: with nothing remembered the pane fell back to the
    # "Select a book" empty state, again ignoring the restored selection.
    controller, ids = library
    workspace = await _render(
        controller, restored=_restored(ids, None), monkeypatch=monkeypatch,
    )
    assert workspace.detail_pane() == "bulk"


async def test_restored_single_selection_still_opens_that_book(
    loop_registered, library, monkeypatch,
):
    controller, ids = library
    workspace = await _render(
        controller, restored=_restored(ids[:1], ids[0]), monkeypatch=monkeypatch,
    )
    assert workspace.detail_pane() == "single"


async def test_open_deep_link_wins_over_a_restored_selection(
    loop_registered, library, monkeypatch,
):
    # ?open=<id> is an explicit request for one book; a remembered selection must not override it.
    controller, ids = library
    workspace = await _render(
        controller, restored=_restored(ids, None), open_book_id=ids[1], monkeypatch=monkeypatch,
    )
    assert workspace.detail_pane() == "single"


async def test_space_deselecting_hands_the_pane_to_the_book_still_selected(
    loop_registered, library, monkeypatch,
):
    # Space toggles the focused row. Dropping to a single remaining selection has to move the
    # pane to THAT book — otherwise it keeps showing the book the user just deselected.
    controller, ids = library
    workspace = await _render(
        controller, restored=_restored(ids, None), monkeypatch=monkeypatch,
    )
    assert workspace.detail_pane() == "bulk"

    workspace.focus_next_row()          # focus (and open) the first row
    await workspace.settle()
    workspace.toggle_focused_row()      # 3 -> 2 selected: still a bulk edit
    await workspace.settle()
    assert workspace.detail_pane() == "bulk"

    workspace.focus_next_row()          # focus (and open) the second row
    await workspace.settle()
    dropped = workspace.detail_title()
    workspace.toggle_focused_row()      # 2 -> 1 selected
    await workspace.settle()

    assert workspace.detail_pane() == "single"
    assert workspace.detail_title() != dropped


async def test_one_selected_book_keeps_the_single_book_action_bar(loop_registered, library):
    controller, _ids = library
    workspace = await _render(controller)
    workspace.select_row(0)
    await workspace.settle()
    assert workspace.detail_pane() == "single"
    assert workspace.action_bar() == ["Save", "Write tags", "Mark ready"]


async def test_selecting_a_second_book_clears_the_single_book_action_bar(
    loop_registered, library,
):
    # Save / Write tags / Mark ready act on one book, and the bar lives OUTSIDE the pane's scroll
    # area — so switching to the bulk editor, which only rebuilt the scrolling part, used to leave
    # the single-book buttons stranded above it.
    controller, _ids = library
    workspace = await _render(controller)
    workspace.select_row(0)
    await workspace.settle()
    workspace.select_row(1)
    await workspace.settle()

    assert workspace.detail_pane() == "bulk"
    assert workspace.action_bar() == []


async def test_bulk_editor_offers_mark_ready(loop_registered, library):
    # Every single-book action has a bulk counterpart except this one, which was simply missing.
    controller, _ids = library
    workspace = await _render(controller)
    workspace.select_row(0)
    workspace.select_row(1)
    await workspace.settle()
    assert "Mark ready" not in workspace.action_bar()   # not the stranded single-book button...
    assert "Mark ready" in workspace.bulk_actions()      # ...a real one in the bulk editor
