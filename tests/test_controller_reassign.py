from colophon.controller import AppController
from tests.test_controller import _ctx


def _mk_book(ctx, folder, *names, title):
    from colophon.core.graph import leaf_id_for
    from colophon.core.models import BookUnit, SourceFile
    paths = [folder / n for n in names]
    b = BookUnit.new(source_folder=folder)
    b.id = leaf_id_for(folder, paths)
    b.source_files = [SourceFile(path=p, size=1, duration_seconds=60.0, ext=".mp3") for p in paths]
    b.title = title
    ctx.books.upsert(b)
    return b


def test_controller_reassign_file_moves_and_persists(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    folder = tmp_path / "Folder"
    folder.mkdir(parents=True)
    a = _mk_book(ctx, folder, "01.mp3", title="A")
    _mk_book(ctx, folder, "02.mp3", "03.mp3", title="B")

    target = ctrl.reassign_file(a, folder / "03.mp3")

    assert {sf.path.name for sf in target.source_files} == {"01.mp3", "03.mp3"}
    assert ctx.grouping.partition(str(folder)) is not None


def test_controller_folder_sibling_files_lists_other_books_files(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    folder = tmp_path / "Folder"
    folder.mkdir(parents=True)
    a = _mk_book(ctx, folder, "01.mp3", title="A")
    _mk_book(ctx, folder, "02.mp3", title="B")

    siblings = ctrl.folder_sibling_files(a)

    assert [(p.name, owner.title) for p, owner in siblings] == [("02.mp3", "B")]
