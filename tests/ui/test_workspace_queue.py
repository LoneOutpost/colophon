"""The Library's Queue scope: the navigator item, cause groups in the middle pane, and bulk
selection over the distinct queued books."""

from pathlib import Path

import pytest
from nicegui import ui
from nicegui.events import GenericEventArguments, handle_event

import colophon.ui.workspace as ws
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


def _queue_scope(kind: str = "queue", filter_text: str = "") -> RestoredView:
    return RestoredView(
        scope={"kind": kind, "key": None}, folder_filter_path=None,
        view={"multiselect": False, "group_by": "author"}, filter_text=filter_text,
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

    # The harness cannot dispatch a browser click, so the button's own click.stop listener is fired.
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
    await _open(workspace, group)
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


async def test_a_lone_book_row_shows_why_it_is_queued(loop_registered, library, monkeypatch):  # noqa: F811
    controller, _folder = library
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    row = _rows(workspace, "Book 2")[0]
    assert "identity weakly backed" in _texts(row)


async def test_a_book_under_two_causes_ticks_in_both_places(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    # Book 0 is blocked (in the folder group) and weakly backed (a lone row): two rows, one book.
    controller, _folder = library
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    await _open(workspace, _group(workspace, "files missing"))
    lone, grouped = _rows(workspace, "Book 0")
    with workspace._client:
        _checkbox(lone).set_value(True)
    await workspace.settle()
    assert _checkbox(grouped).value is True


async def test_an_open_group_stays_open_through_an_action_repaint(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    controller, _folder = library
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    await _open(workspace, _group(workspace, "files missing"))
    workspace.click("Select all")          # repaints the list, as Space or Mark ready does
    await workspace.settle()
    group = _group(workspace, "files missing")
    assert group.value is True
    assert {"Book 0", "Book 1"} <= set(_texts(group))


async def test_check_matches_lists_the_worst_fit_first_whatever_the_sort(
    loop_registered, tmp_path, monkeypatch,  # noqa: F811
):
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    for title, fit in (("Alpha", 60.0), ("Bravo", 20.0), ("Charlie", 40.0)):
        book = BookUnit.new(source_folder=tmp_path / "ingest" / title)
        book.title, book.authors, book.confidence = title, ["Author A"], fit
        ctx.books.upsert(book)
    workspace = await _render(AppController(ctx), restored=_queue_scope("check_matches"),
                              monkeypatch=monkeypatch)
    titles = [e.text for e in workspace._of("Label") if "colophon-book-title" in e._classes]
    assert titles == ["Bravo", "Charlie", "Alpha"]    # the view sort is Title A-Z
    ctx.close()


async def test_an_empty_queue_says_nothing_needs_you(loop_registered, tmp_path, monkeypatch):  # noqa: F811
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    workspace = await _render(AppController(ctx), restored=_queue_scope(), monkeypatch=monkeypatch)
    assert "Nothing needs you" in workspace.labels()
    ctx.close()


async def test_a_filtered_empty_queue_says_the_filters_hide_it(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    controller, _folder = library
    workspace = await _render(controller, restored=_queue_scope(filter_text="zzzz"),
                              monkeypatch=monkeypatch)
    labels = workspace.labels()
    assert "No queued books match your filters" in labels
    assert "Nothing needs you" not in labels


async def test_leaving_the_queue_for_all_books_shows_windowed_book_rows(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    controller, _folder = library
    monkeypatch.setattr(ws, "_PAGE", 2)
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    assert _group(workspace, "files missing") is not None
    nav = next(e for e in workspace._of("Item")
               if any(t == "All books" for t in _texts(e)))
    for listener in list(nav._event_listeners.values()):
        if listener.type == "click":
            handle_event(listener.handler,
                         GenericEventArguments(sender=nav, client=workspace._client, args={}))
    await workspace.settle()
    assert not [e for e in workspace._of("Expansion") if "colophon-queue-group" in e._classes]
    assert len([e for e in workspace._of("Item") if "book-row" in e._classes]) == 2
    assert "Showing 2 of 3; scroll for more" in workspace.labels()


def _group(workspace, phrase: str):
    return next((e for e in workspace._of("Expansion") if "colophon-queue-group" in e._classes
                 and any(phrase in t for t in _texts(e))), None)


async def _open(workspace, group) -> None:
    with workspace._client:
        group.set_value(True)
    await workspace.settle()


def _rows(workspace, title: str) -> list:
    """Every book row showing `title`, in the order they were built."""
    return [e for e in workspace._of("Item") if "book-row" in e._classes and title in _texts(e)]


def _checkbox(row):
    found = []

    def walk(element) -> None:
        for slot in element.slots.values():
            for child in slot.children:
                if type(child).__name__ == "Checkbox":
                    found.append(child)
                walk(child)

    walk(row)
    return found[0]


def _texts(element) -> list[str]:
    """Every label text under `element`, across all its slots."""
    out: list[str] = []
    for slot in element.slots.values():
        for child in slot.children:
            if type(child).__name__ in ("Label", "ItemLabel"):
                out.append(child.text)
            out.extend(_texts(child))
    return out

