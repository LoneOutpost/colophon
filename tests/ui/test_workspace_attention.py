"""The Attention pane's one-click fix for a conflict finding: Use "<the folder's value>"."""

from pathlib import Path

import pytest
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
