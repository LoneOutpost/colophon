from pathlib import Path

from colophon.adapters.repository.store import (
    BookUnitRepo,
    GroupingOverrideRepo,
    connect,
    migrate,
)
from colophon.core.models import BookUnit, ContentKind, SourceFile
from colophon.services.combine import combine_books, uncombine_books


def _repos(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn), GroupingOverrideRepo(conn)


def _single_book(folder: Path, name: str, *, conf: float = 0.0, title: str | None = None) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.id = b.id + name  # distinct leaf ids sharing a folder (an over-split folder)
    b.source_files = [SourceFile(path=folder / name, size=1, duration_seconds=60.0, ext=".mp3")]
    b.identity_confidence = conf
    b.title = title
    return b


def test_grouping_override_round_trip(tmp_path):
    _, grouping = _repos(tmp_path)
    grouping.set_single("/lib/Book", snapshot='[{"x": 1}]')
    assert grouping.is_single("/lib/Book")
    assert grouping.single_folders() == frozenset({"/lib/Book"})
    assert grouping.snapshot("/lib/Book") == '[{"x": 1}]'
    grouping.clear("/lib/Book")
    assert not grouping.is_single("/lib/Book")
    assert grouping.single_folders() == frozenset()


def test_combine_merges_files_in_order_under_primary(tmp_path):
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "The Stand"
    # three files wrongly split into three books; "02" is the highest-confidence -> primary
    b1 = _single_book(folder, "01.mp3", conf=0.1, title="Chapter 1")
    b2 = _single_book(folder, "02.mp3", conf=0.9, title="The Stand")
    b10 = _single_book(folder, "10.mp3", conf=0.1, title="Chapter 10")
    for b in (b1, b2, b10):
        books.upsert(b)

    merged = combine_books(books, grouping, folder, [b1, b2, b10])

    assert merged.id == BookUnit.id_for(folder)          # sole book of the folder
    assert merged.title == "The Stand"                   # primary (highest confidence) wins
    assert merged.content_kind is ContentKind.SINGLE
    assert [sf.path.name for sf in merged.source_files] == ["01.mp3", "02.mp3", "10.mp3"]  # natural order
    assert len(merged.chapters) == 3                     # one chapter per file
    # the three source books are gone; only the merged book remains
    assert {b.id for b in books.list_all()} == {merged.id}
    assert grouping.is_single(str(folder))


def test_partition_override_round_trip(tmp_path):
    _, grouping = _repos(tmp_path)
    groups = [["01.mp3", "02.mp3"], ["03.mp3"]]
    grouping.set_partition("/lib/Folder", groups)
    assert grouping.partition("/lib/Folder") == groups
    assert grouping.partitioned_folders() == {"/lib/Folder": groups}


def test_set_partition_and_set_single_are_mutually_exclusive(tmp_path):
    _, grouping = _repos(tmp_path)
    grouping.set_single("/lib/Folder", snapshot="[]")
    grouping.set_partition("/lib/Folder", [["01.mp3"], ["02.mp3"]])
    assert not grouping.is_single("/lib/Folder")            # single replaced
    assert grouping.partition("/lib/Folder") == [["01.mp3"], ["02.mp3"]]
    grouping.set_single("/lib/Folder", snapshot="[]")
    assert grouping.partition("/lib/Folder") is None        # partition replaced
    assert grouping.is_single("/lib/Folder")


def test_uncombine_restores_the_separate_books(tmp_path):
    books, grouping = _repos(tmp_path)
    folder = tmp_path / "Split Me"
    originals = [
        _single_book(folder, "a.mp3", title="A"),
        _single_book(folder, "b.mp3", title="B"),
    ]
    for b in originals:
        books.upsert(b)
    combine_books(books, grouping, folder, originals)
    assert len(books.list_all()) == 1

    restored = uncombine_books(books, grouping, folder)

    assert {b.id for b in restored} == {b.id for b in originals}
    assert {b.id for b in books.list_all()} == {b.id for b in originals}
    assert {b.title for b in books.list_all()} == {"A", "B"}
    assert not grouping.is_single(str(folder))
