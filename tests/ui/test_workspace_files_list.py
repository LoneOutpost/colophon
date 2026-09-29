"""The Details pane's Files list is paged: a book with a thousand files must not build a thousand
rows of buttons in one go (it stalled the page past NiceGUI's disconnect), and a file action must
not snap the list back to its first page."""

from pathlib import Path

import pytest
from nicegui.events import GenericEventArguments, handle_event

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, SourceFile
from tests.ui.test_workspace_selection import _render, _Workspace, loop_registered  # noqa: F401


@pytest.fixture
def library(tmp_path: Path):
    """Two big books (120 files each) and a small one (10 files)."""
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    controller = AppController(ctx)
    ids = {}
    for name, count in (("big", 120), ("other", 120), ("small", 10)):
        src = tmp_path / "ingest" / "Author A" / name
        src.mkdir(parents=True)
        book = BookUnit.new(source_folder=src)
        book.title, book.authors = name, ["Author A"]
        book.source_files = [
            SourceFile(path=src / f"{i:04d}.mp3", size=1, duration_seconds=1.0, ext="mp3")
            for i in range(count)
        ]
        ctx.books.upsert(book)
        ids[name] = book.id
    yield controller, ids
    ctx.close()


def _file_rows(workspace: _Workspace) -> list:
    return [e for e in list(workspace._client.elements.values())
            if "colophon-file-row" in e._classes]


def _press(workspace: _Workspace, button) -> None:
    for listener in list(button._event_listeners.values()):
        if listener.type == "click":
            handle_event(listener.handler,
                         GenericEventArguments(sender=button, client=workspace._client, args={}))


def _row_button(workspace: _Workspace, aria_label: str, row: int):
    return [e for e in workspace._of("Button") if e._props.get("aria-label") == aria_label][row]


async def test_a_many_file_book_renders_one_page_and_a_show_more(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["big"])
    assert len(_file_rows(workspace)) == 50
    assert "Files (120)" in workspace.labels()
    assert "Showing 50 of 120 files" in workspace.labels()
    workspace.click("Show 50 more")
    await workspace.settle()
    assert len(_file_rows(workspace)) == 100
    assert "Showing 100 of 120 files" in workspace.labels()
    workspace.click("Show 20 more")
    await workspace.settle()
    assert len(_file_rows(workspace)) == 120
    # The Files footer is gone; the fallback chapter list (one per file) pages on its own.
    assert not any((t or "").endswith(" of 120 files") for t in workspace.labels())
    assert "Showing 50 of 120 chapters" in workspace.labels()


async def test_a_file_action_keeps_the_expanded_window(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["big"])
    workspace.click("Show 50 more")
    await workspace.settle()
    moved = controller.get_book(ids["big"]).source_files[60].path
    _press(workspace, _row_button(workspace, "Move file down", 60))
    await workspace.settle()
    assert controller.get_book(ids["big"]).source_files[61].path == moved
    assert len(_file_rows(workspace)) >= 100


async def test_moving_the_last_shown_file_down_keeps_it_in_view(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["big"])
    _press(workspace, _row_button(workspace, "Move file down", 49))
    await workspace.settle()
    assert len(_file_rows(workspace)) >= 51


async def test_the_arrows_reflect_the_whole_list_not_the_window(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["big"])
    # Row 49 is the last one shown but not the last file, so it can still move down.
    assert _row_button(workspace, "Move file down", 49).enabled
    assert not _row_button(workspace, "Move file up", 0).enabled


async def test_switching_books_resets_the_window(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["big"])
    workspace.click("Show 50 more")
    await workspace.settle()
    workspace.click_row("other")
    await workspace.settle()
    assert workspace.detail_title() == "other"
    assert len(_file_rows(workspace)) == 50


async def test_a_small_book_shows_every_file_and_no_button(loop_registered, library):  # noqa: F811
    controller, ids = library
    workspace = await _render(controller, open_book_id=ids["small"])
    assert len(_file_rows(workspace)) == 10
    assert not any(b.text.endswith(" more") for b in workspace._of("Button"))
    assert not any((t or "").startswith("Showing ") and t.endswith(" files")
                   for t in workspace.labels())


@pytest.fixture
def shared_folder(tmp_path: Path):
    """Two books in one folder: a small one, and a 120-file one whose files are its siblings."""
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    controller = AppController(ctx)
    src = tmp_path / "ingest" / "Author A" / "Shelf"
    src.mkdir(parents=True)
    ids = {}
    for name, count in (("shelf-a", 3), ("shelf-b", 120)):
        book = BookUnit.new(source_folder=src)
        book.id = f"{book.id}-{name}"  # clustered books share a folder, so not the folder's id
        book.title, book.authors = name, ["Author A"]
        book.source_files = [
            SourceFile(path=src / f"{name}-{i:04d}.mp3", size=1, duration_seconds=1.0, ext="mp3")
            for i in range(count)
        ]
        ctx.books.upsert(book)
        ids[name] = book.id
    yield controller, ids
    ctx.close()


async def test_a_large_sibling_list_pages_too(loop_registered, shared_folder):  # noqa: F811
    controller, ids = shared_folder

    def siblings(workspace):
        return [e for e in list(workspace._client.elements.values())
                if "colophon-sibling-row" in e._classes]

    workspace = await _render(controller, open_book_id=ids["shelf-a"])
    assert len(_file_rows(workspace)) == 3
    assert len(siblings(workspace)) == 50
    assert "Showing 50 of 120 files" in workspace.labels()
    workspace.click("Show 50 more")
    await workspace.settle()
    assert len(siblings(workspace)) == 100


async def test_a_long_chapter_list_pages_and_edit_still_gets_every_chapter(
    loop_registered, library, monkeypatch,  # noqa: F811
):
    import colophon.ui.workspace as ws
    from colophon.core.models import Chapter

    controller, ids = library
    book = controller.get_book(ids["small"])
    book.chapters = [Chapter(title=f"Ch {i}", start_ms=i * 1000, end_ms=(i + 1) * 1000)
                     for i in range(120)]
    controller.ctx.books.upsert(book)
    edited = {}
    monkeypatch.setattr(ws, "chapter_edit_dialog",
                        lambda _c, _b, chs, **_kw: edited.update(n=len(chs)))

    workspace = await _render(controller, open_book_id=ids["small"])
    rows = [e for e in list(workspace._client.elements.values())
            if "colophon-chapter-row" in e._classes]
    assert len(rows) == 50
    assert "Showing 50 of 120 chapters" in workspace.labels()
    workspace.click("Show 50 more")
    await workspace.settle()
    rows = [e for e in list(workspace._client.elements.values())
            if "colophon-chapter-row" in e._classes]
    assert len(rows) == 100
    workspace.click("Edit")
    assert edited["n"] == 120
