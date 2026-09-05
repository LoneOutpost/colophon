"""The Persist dialog must stay responsive while it computes what a persist would do.

On a large library the destination preview is tens of thousands of filesystem calls. Run inline it
starved the event loop for minutes, so NiceGUI's socket heartbeat went unanswered, the client was
dropped, and the reconnect reload took the dialog — and the pending persist — with it.
"""

import asyncio
import time
from pathlib import Path

import pytest
from nicegui import core
from nicegui.client import Client
from nicegui.events import GenericEventArguments
from nicegui.page import page

from colophon.core.models import BookUnit
from colophon.ui.dialogs import persist_dialog

_TICK = 0.01          # heartbeat period: stands in for the browser's socket ping
_PREVIEW_WORK = 0.3   # how long the stubbed preview blocks its thread


class _FakeConfig:
    organize_folder_pattern = "$Author/$Title"
    organize_file_pattern = "$Title"
    series_pattern = ""
    series_name_pattern = ""
    series_number_pattern = ""
    recent_organize_patterns: list = []  # noqa: RUF012 - test stub, never mutated
    reorg_delete_sources = False


class _FakeContext:
    config = _FakeConfig()


class _FakeController:
    """Just enough controller for the dialog's options screen and one preview."""

    ctx = _FakeContext()

    def __init__(self, book: BookUnit) -> None:
        self.book = book
        self.previews = 0

    def scope_counts(self, **_kwargs) -> dict[str, int]:
        return {"ready": 1, "total": 1}

    def books_for_scope(self, *_args, **_kwargs) -> list[BookUnit]:
        return [self.book]

    def record_organize_pattern(self, *_args) -> None:
        pass

    def organize_preview(self, _books, **_kwargs) -> list:
        self.previews += 1
        time.sleep(_PREVIEW_WORK)  # stands in for stat-ing every file of every book in scope
        return []


@pytest.fixture
async def client():
    """A throwaway NiceGUI client to build into, with the running loop registered.

    Without the loop NiceGUI defers click handlers instead of running them, and without an entered
    client there is no slot for the dialog's elements to land in.
    """
    previous = core.loop
    core.loop = asyncio.get_running_loop()
    yield Client(page("/"))
    core.loop = previous


class _Screen:
    """The elements of one open dialog, addressed by kind and label."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def find(self, kind: str, text: str | None = None) -> list:
        return [
            e for e in self._client.elements.values()
            if type(e).__name__ == kind and (text is None or e.text == text)
        ]

    def click(self, kind: str, text: str) -> None:
        button = self.find(kind, text)[0]
        for listener in button._event_listeners.values():
            if listener.type == "click":
                listener.handler(
                    GenericEventArguments(sender=button, client=self._client, args={})
                )

    async def settle(self, ticks: int = 300) -> None:
        """Yield to the loop until the preview screen appears (or we give up)."""
        for _ in range(ticks):
            await asyncio.sleep(_TICK)
            if self.find("Label", "Confirm destinations"):
                return


async def _open_organize_persist(client: Client, controller: _FakeController) -> _Screen:
    """Open the dialog and tick Organize, so Persist leads to the destination preview."""
    with client:  # entered here, in the test's own task, so the dialog's elements have a slot
        await persist_dialog(
            controller, refresh_all=lambda: None,
            selected_ids=set(), clear_selection=lambda: None,
        )
    screen = _Screen(client)
    screen.find("Checkbox", "Organize — place into the library")[0].set_value(True)
    return screen


def _book() -> BookUnit:
    book = BookUnit.new(source_folder=Path("/tmp/ingest/Dune"))
    book.title = "Dune"
    return book


async def test_persist_preview_does_not_block_the_event_loop(client):
    screen = await _open_organize_persist(client, _FakeController(_book()))
    beats = {"n": 0}

    async def heartbeat() -> None:
        while True:
            beats["n"] += 1
            await asyncio.sleep(_TICK)

    pulse = asyncio.create_task(heartbeat())
    try:
        screen.click("Button", "Persist")
        await screen.settle()
    finally:
        pulse.cancel()

    assert screen.find("Label", "Confirm destinations"), "the preview screen never rendered"
    # Computed inline, the blocking preview would let through at most a beat or two; off the loop
    # the heartbeat keeps its cadence for the whole of it.
    assert beats["n"] >= _PREVIEW_WORK / _TICK / 2


async def test_persist_button_shows_it_is_working_and_runs_once(client):
    # The preview is slow-but-responsive now, so the click has to say so — and an impatient second
    # click must not start a second one.
    controller = _FakeController(_book())
    screen = await _open_organize_persist(client, controller)

    screen.click("Button", "Persist")
    await asyncio.sleep(_TICK)
    button = screen.find("Button", "Persist")[0]
    assert button._props.get("loading") == "true"   # spinner while the preview computes
    assert button._props.get("disable") is True     # and no second click lands

    screen.click("Button", "Persist")               # ...even if one is dispatched anyway
    await screen.settle()
    assert controller.previews == 1
