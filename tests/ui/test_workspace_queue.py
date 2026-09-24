"""The Library's Queue scope: the navigator item, cause groups in the middle pane, and bulk
selection over the distinct queued books."""

from pathlib import Path

import pytest
from nicegui import ui
from nicegui.events import GenericEventArguments, handle_event

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, SourceFile
from colophon.core.view_state import RestoredView
from colophon.ui.graph_view import folder_tree_url
from tests.ui.test_workspace_selection import _render, loop_registered  # noqa: F401


@pytest.fixture
def library(tmp_path: Path):
    """Three unidentified books under one author folder; two of them are missing from disk."""
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
        book.missing = i < 2
        ctx.books.upsert(book)
    yield controller, tmp_path / "ingest" / "Author A"
    ctx.close()


def _queue_scope() -> RestoredView:
    return RestoredView(
        scope={"kind": "queue", "key": None}, folder_filter_path=None,
        view={"multiselect": False, "group_by": "author"}, filter_text="",
        selected_ids=set(), open_book_id=None,
    )


async def test_navigator_counts_each_queued_book_once(loop_registered, library):  # noqa: F811
    controller, _folder = library
    queue = controller.review_queue()
    assert sum(len(g.books) for g in queue.groups) > queue.book_count == 3  # overlapping causes
    workspace = await _render(controller)
    assert "Queue (3)" in [e.text for e in workspace._of("ItemLabel")]


async def test_a_folder_cause_is_one_group_with_review_folder(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    controller, folder = library
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    labels = workspace.labels()
    assert "2 books under Author A: files missing from disk" in labels

    navigated: list[str] = []
    monkeypatch.setattr(ui.navigate, "to", navigated.append)
    button = workspace.button("Review folder")
    listener = next(v for v in button._event_listeners.values() if v.type == "click.stop")
    handle_event(listener.handler,
                 GenericEventArguments(sender=button, client=workspace._client, args={}))
    assert navigated == [folder_tree_url(folder)]

    # A group's rows are built when it is first opened, not up front.
    group = next(e for e in workspace._of("Expansion") if "colophon-queue-group" in e._classes
                 and any("files missing" in t for t in _texts(e)))
    assert "Book 0" not in _texts(group)
    with workspace._client:
        group.set_value(True)
    await workspace.settle()
    assert {"Book 0", "Book 1"} <= set(_texts(group))


async def test_select_all_in_the_queue_selects_the_distinct_queued_books(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    # Two of the books sit in two groups each (blocked, and weakly backed); each is selected once.
    controller, _folder = library
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    workspace.click("Select all")   # the Books-header one: every book the Queue scope shows
    await workspace.settle()
    assert workspace.detail_pane() == "bulk"
    assert "Editing 3 books" in workspace.labels()


def _texts(element) -> list[str]:
    """Every label text under `element`, across all its slots."""
    out: list[str] = []
    for slot in element.slots.values():
        for child in slot.children:
            if type(child).__name__ in ("Label", "ItemLabel"):
                out.append(child.text)
            out.extend(_texts(child))
    return out

