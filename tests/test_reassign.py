from pathlib import Path

from colophon.adapters.repository.store import (
    BookUnitRepo,
    GroupingOverrideRepo,
    connect,
    migrate,
)
from colophon.core.models import BookUnit, SourceFile
from colophon.services.reassign import reassign_file


def _repos(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn), GroupingOverrideRepo(conn)


def _book(folder: Path, *names: str, title: str | None = None) -> BookUnit:
    from colophon.core.graph import leaf_id_for
    paths = [folder / n for n in names]
    b = BookUnit.new(source_folder=folder)
    b.id = leaf_id_for(folder, paths)
    b.source_files = [SourceFile(path=p, size=1, duration_seconds=60.0, ext=".mp3") for p in paths]
    b.title = title
    return b


def test_reassign_moves_file_to_target_and_updates_both(tmp_path):
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "Folder"
    a = _book(folder, "01.mp3", title="Book A")
    b = _book(folder, "02.mp3", "03.mp3", title="Book B")
    books.upsert(a)
    books.upsert(b)

    target = reassign_file(books, grouping, folder, folder / "03.mp3", a.id)

    assert target.title == "Book A"
    assert {sf.path.name for sf in target.source_files} == {"01.mp3", "03.mp3"}
    remaining = [books.get(i) for i in books.ids_in_folder(folder)]
    by_files = {frozenset(sf.path.name for sf in bk.source_files) for bk in remaining}
    assert by_files == {frozenset({"01.mp3", "03.mp3"}), frozenset({"02.mp3"})}
    part = grouping.partition(str(folder))
    assert {frozenset(g) for g in part} == {frozenset({"01.mp3", "03.mp3"}), frozenset({"02.mp3"})}


def test_reassign_emptying_source_removes_it(tmp_path):
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "Folder"
    a = _book(folder, "01.mp3", title="Book A")
    b = _book(folder, "02.mp3", title="Book B")
    books.upsert(a)
    books.upsert(b)

    reassign_file(books, grouping, folder, folder / "02.mp3", a.id)

    remaining = [books.get(i) for i in books.ids_in_folder(folder)]
    assert len(remaining) == 1
    assert {sf.path.name for sf in remaining[0].source_files} == {"01.mp3", "02.mp3"}


def test_reassign_leaves_uninvolved_sibling_untouched(tmp_path):
    # Three books share the folder; moving a file between two of them must leave the third book
    # (its id and files) untouched and still present in the persisted partition.
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "Folder"
    a = _book(folder, "01.mp3", title="A")
    b = _book(folder, "02.mp3", title="B")
    c = _book(folder, "03.mp3", "04.mp3", title="C")
    books.upsert(a)
    books.upsert(b)
    books.upsert(c)

    reassign_file(books, grouping, folder, folder / "02.mp3", a.id)

    assert books.get(c.id) is not None  # uninvolved sibling untouched (same id)
    by_files = {
        frozenset(sf.path.name for sf in books.get(i).source_files)
        for i in books.ids_in_folder(folder)
    }
    assert by_files == {frozenset({"01.mp3", "02.mp3"}), frozenset({"03.mp3", "04.mp3"})}
    part = grouping.partition(str(folder))
    assert {frozenset(g) for g in part} == {
        frozenset({"01.mp3", "02.mp3"}), frozenset({"03.mp3", "04.mp3"})
    }


def test_reassign_noop_when_file_already_in_target(tmp_path):
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "Folder"
    a = _book(folder, "01.mp3", "02.mp3", title="A")
    books.upsert(a)

    result = reassign_file(books, grouping, folder, folder / "01.mp3", a.id)

    assert result.id == a.id
    assert {sf.path.name for sf in result.source_files} == {"01.mp3", "02.mp3"}
    assert grouping.partition(str(folder)) is None  # a no-op writes no override
