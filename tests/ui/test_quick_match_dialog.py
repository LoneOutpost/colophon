"""Quick Match over a large selection must not flood the browser in one push.

Matching ~180 books built every proposal row up front: ~17 NiceGUI elements per book plus one
remote cover image each, all created synchronously and pushed as a single websocket message. The
browser could not render that and answer the socket ping in time, so the server dropped the client
and the reconnect reloaded the page — taking the dialog, and the un-accepted matches, with it.
"""

import asyncio
import time
from pathlib import Path

import pytest
from nicegui import core
from nicegui.client import Client
from nicegui.events import GenericEventArguments, handle_event
from nicegui.page import page

import colophon.ui.dialogs as dlg
from colophon.core.models import BookUnit, SourceFile
from colophon.core.quickmatch import QuickMatchProposal
from colophon.core.sources import SourceResult

_TICK = 0.01


@pytest.fixture
async def client():
    """A throwaway NiceGUI client, with the running loop registered so handlers actually run."""
    previous = core.loop
    core.loop = asyncio.get_running_loop()
    yield Client(page("/"))
    core.loop = previous


def _proposals(n: int) -> list[QuickMatchProposal]:
    out = []
    for i in range(n):
        book = BookUnit.new(source_folder=Path(f"/x/Book {i}"))
        book.title, book.authors = f"Book {i}", ["Author A"]
        book.source_files = [
            SourceFile(path=Path(f"/x/Book {i}/a.mp3"), size=1, duration_seconds=3600.0, ext="mp3")
        ]
        result = SourceResult(
            provider="audnexus", title=f"Book {i}: The Real Title", authors=["Author A"],
            narrators=["Some Narrator"], series_name="A Series", series_sequence=float(i),
            publish_year=2001, publisher="A Publisher",
            cover_url="https://example.invalid/cover.jpg",
        )
        out.append(QuickMatchProposal(book=book, best=result, results=[result], confidence=88.0))
    return out


class _StubController:
    """Just enough controller for the dialog; the scan itself is already properly async."""

    def __init__(self, n: int) -> None:
        self.proposals = _proposals(n)
        self.applied: list = []

    def available_sources(self):
        return [("audnexus", "Audnexus")]

    def review_threshold(self):
        return 75.0

    def source_label(self, provider):
        return provider.title()

    def source_tooltip(self, provider):
        return provider

    async def quick_match_scan(self, books, source_names, search_fields, **_kwargs):
        await asyncio.sleep(0)
        return self.proposals

    def quick_match_apply(self, chosen):
        self.applied = list(chosen)
        raise AssertionError("quick_match_apply must run off the event loop")


class _Screen:
    def __init__(self, client: Client) -> None:
        self._client = client

    def of(self, kind: str, text: str | None = None) -> list:
        return [
            e for e in list(self._client.elements.values())
            if type(e).__name__ == kind and (text is None or getattr(e, "text", None) == text)
        ]

    def click(self, text: str) -> None:
        button = next(e for e in self.of("Button") if e.text == text)
        for listener in list(button._event_listeners.values()):
            if listener.type == "click":
                handle_event(
                    listener.handler,
                    GenericEventArguments(sender=button, client=self._client, args={}),
                )

    async def settle(self, until: str, ticks: int = 400) -> None:
        for _ in range(ticks):
            await asyncio.sleep(_TICK)
            if self.of("Button", until):
                return


async def _search(client: Client, n: int) -> tuple[_Screen, _StubController, int]:
    """Open the dialog over `n` books, run the search, and count loop ticks while it renders."""
    controller = _StubController(n)
    with client:
        await dlg.quick_match_dialog(
            controller, [p.book for p in controller.proposals], clear_selection=lambda: None
        )
    screen = _Screen(client)
    beats = {"n": 0}

    async def pulse() -> None:
        while True:
            beats["n"] += 1
            await asyncio.sleep(_TICK)

    task = asyncio.create_task(pulse())
    try:
        screen.click("Search")
        await screen.settle(until="Apply selected")
    finally:
        task.cancel()
    return screen, controller, beats["n"]


async def test_a_large_match_does_not_build_a_row_per_book(client):
    # 180 books used to mean ~3,100 elements and a ~400 KB single push. A window keeps the first
    # screenful only; the rest arrive on demand.
    screen, _controller, _beats = await _search(client, 180)
    assert screen.of("Button", "Apply selected"), "the preview never rendered"

    rows = len(screen.of("Expansion"))
    assert rows <= dlg._MATCH_PAGE, f"rendered {rows} proposal rows in one push"


async def test_the_rest_of_the_matches_are_reachable(client):
    # Windowing must not hide matches: the count is stated and more can be shown.
    screen, _controller, _beats = await _search(client, 180)
    assert any("180" in (e.text or "") for e in screen.of("Label")), \
        "the preview does not say how many matches there are"

    before = len(screen.of("Expansion"))
    screen.click("Show more")
    await asyncio.sleep(_TICK)
    assert len(screen.of("Expansion")) > before


async def test_covers_do_not_all_fetch_at_once(client):
    # Every row carried a remote <img>; 180 of them at once is what the browser choked on.
    screen, _controller, _beats = await _search(client, 180)
    images = screen.of("Image")
    assert len(images) <= dlg._MATCH_PAGE
    assert all(i._props.get("loading") == "lazy" for i in images), \
        "off-screen covers still fetch eagerly"


async def test_a_small_match_still_renders_everything(client):
    screen, _controller, _beats = await _search(client, 5)
    assert len(screen.of("Expansion")) == 5
    assert not screen.of("Button", "Show more")


async def test_applying_a_large_selection_runs_off_the_event_loop(client):
    # Applying writes every matched book; inline that starves the socket the same way the render
    # did, and the user loses the result right after confirming it.
    screen, controller, _beats = await _search(client, 20)

    class _Summary:
        applied_count, now_ready_count, batch_id = 20, 20, "b1"

    def _slow_apply(chosen):
        time.sleep(0.3)
        return _Summary()

    controller.quick_match_apply = _slow_apply
    beats = {"n": 0}

    async def pulse() -> None:
        while True:
            beats["n"] += 1
            await asyncio.sleep(_TICK)

    task = asyncio.create_task(pulse())
    try:
        screen.click("Apply selected")
        for _ in range(200):
            await asyncio.sleep(_TICK)
            if any("Applied" in (e.text or "") for e in screen.of("Label")):
                break
    finally:
        task.cancel()

    assert any("Applied" in (e.text or "") for e in screen.of("Label")), "the apply never finished"
    assert beats["n"] >= 0.3 / _TICK / 2, "the event loop stalled while applying"
