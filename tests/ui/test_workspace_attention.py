"""The Attention pane's one-click fixes for a conflict finding: Use "<the folder's value>", and for a
folder-name conflict, Use folder name "<the folder's name>"."""

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TPE1
from nicegui.events import GenericEventArguments, handle_event

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import (
    BookUnit,
    Finding,
    FindingCode,
    FindingSeverity,
    Provenance,
    SourceFile,
)
from tests.ui.test_workspace_queue import _queue_scope
from tests.ui.test_workspace_selection import _render, loop_registered  # noqa: F401


@pytest.fixture
def conflicted(tmp_path: Path):
    """One book whose album tag ('Ph1') disagrees with its folder ('Porterhouse Blue')."""
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"],
    ))
    src = tmp_path / "ingest" / "Tom Sharpe" / "Porterhouse Blue"
    src.mkdir(parents=True)
    (src / "a.mp3").write_bytes(b"")
    book = BookUnit.new(source_folder=src)
    book.title, book.authors = "Ph1", ["Tom Sharpe"]
    book.provenance["title"] = Provenance.TAG.value
    book.source_files = [SourceFile(path=src / "a.mp3", size=1, duration_seconds=1.0, ext="mp3")]
    book.findings = [Finding(
        code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
        detail='folder "Porterhouse Blue" vs title "Ph1" (from the album tag)',
        field="title", current="Ph1", suggested="Porterhouse Blue", source="album tag",
    )]
    ctx.books.upsert(book)
    yield AppController(ctx), book.id
    ctx.close()


async def test_use_the_folders_value_fixes_the_title_and_clears_the_finding(
    loop_registered, conflicted,  # noqa: F811
):
    controller, book_id = conflicted
    workspace = await _render(controller, open_book_id=book_id)
    assert 'folder "Porterhouse Blue" vs title "Ph1" (from the album tag)' in workspace.labels()

    workspace.click('Use "Porterhouse Blue"')
    await workspace.settle()

    stored = controller.get_book(book_id)
    assert stored.title == "Porterhouse Blue"
    assert stored.provenance["title"] == Provenance.MANUAL.value
    # The pane repainted: the finding is settled, so neither its detail nor the button remain.
    assert not any(text and text.startswith('folder "Porterhouse Blue"')
                   for text in workspace.labels())
    assert not any(b.text == 'Use "Porterhouse Blue"' for b in workspace._of("Button"))
    assert workspace.detail_title() == "Porterhouse Blue"


async def test_at_a_glance_offers_the_same_fix(loop_registered, conflicted):  # noqa: F811
    controller, book_id = conflicted
    workspace = await _render(controller, open_book_id=book_id)
    tabs = next(t for t in workspace._of("Tabs") if t.value == "details")
    tabs.set_value("state")
    await workspace.settle()
    # At a Glance draws its action buttons dense, which tells its button from the Details pane's.
    [glance] = [b for b in workspace._of("Button")
                if b.text == 'Use "Porterhouse Blue"' and b._props.get("dense")]
    for listener in list(glance._event_listeners.values()):
        if listener.type == "click":
            handle_event(listener.handler,
                         GenericEventArguments(sender=glance, client=glance.client, args={}))
    await workspace.settle()
    assert controller.get_book(book_id).title == "Porterhouse Blue"


@pytest.fixture
def misnamed(tmp_path: Path):
    """A 'Neal Stephenson' folder whose books' tags all elect 'Top 100 Sci-Fi Books', scanned."""
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest],
    ))
    author = ingest / "Neal Stephenson"
    for title in ("Cryptonomicon", "Anathem"):
        folder = author / f"Neal Stephenson.-.{title}"
        folder.mkdir(parents=True)
        f = folder / "01.mp3"
        f.write_bytes(b"")
        tags = ID3()
        tags.add(TPE1(encoding=3, text=["Top 100 Sci-Fi Books"]))
        tags.save(f)
    controller = AppController(ctx)
    controller.scan([ingest])
    books = [b for b in ctx.books.list_all() if author in b.source_folder.parents]
    yield controller, author, books
    ctx.close()


def _all_take_the_folder_name(controller, books) -> bool:
    return all(controller.get_book(b.id).authors == ["Neal Stephenson"] for b in books)


async def test_use_folder_name_makes_the_folder_the_author_of_every_book_under_it(
    loop_registered, misnamed,  # noqa: F811
):
    controller, _author, books = misnamed
    workspace = await _render(controller, open_book_id=books[0].id)
    workspace.click('Use folder name "Neal Stephenson"')
    await workspace.settle()

    assert _all_take_the_folder_name(controller, books)
    assert not any(b.text == 'Use folder name "Neal Stephenson"' for b in workspace._of("Button"))


async def test_a_folder_name_queue_group_offers_the_folder_name(
    loop_registered, misnamed, monkeypatch,  # noqa: F811
):
    controller, _author, books = misnamed
    workspace = await _render(controller, restored=_queue_scope(), monkeypatch=monkeypatch)
    button = workspace.button('Use "Neal Stephenson"')
    assert button._props["aria-label"] == "Use the folder name Neal Stephenson as the author"
    # The harness cannot dispatch a browser click, so the button's own click.stop listener is fired.
    listener = next(v for v in button._event_listeners.values() if v.type == "click.stop")
    handle_event(listener.handler,
                 GenericEventArguments(sender=button, client=workspace._client, args={}))
    await workspace.settle()

    assert _all_take_the_folder_name(controller, books)
    assert not any(b.text == 'Use "Neal Stephenson"' for b in workspace._of("Button"))


async def test_at_a_glance_offers_the_folder_name_too(loop_registered, misnamed):  # noqa: F811
    controller, _author, books = misnamed
    workspace = await _render(controller, open_book_id=books[0].id)
    tabs = next(t for t in workspace._of("Tabs") if t.value == "details")
    tabs.set_value("state")
    await workspace.settle()
    [glance] = [b for b in workspace._of("Button")
                if b.text == 'Use folder name "Neal Stephenson"' and b._props.get("dense")]
    for listener in list(glance._event_listeners.values()):
        if listener.type == "click":
            handle_event(listener.handler,
                         GenericEventArguments(sender=glance, client=glance.client, args={}))
    await workspace.settle()
    assert _all_take_the_folder_name(controller, books)
