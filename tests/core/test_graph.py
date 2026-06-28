
from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole
from colophon.core.models import BookUnit


def test_file_node_role_and_id(tmp_path):
    fn = FileNode(path=tmp_path / "a.mp3", role=FileRole.AUDIO)
    assert fn.role is FileRole.AUDIO
    assert fn.id == FileNode.id_for(tmp_path / "a.mp3")


def test_book_node_embeds_a_book(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "Dune")
    node = BookNode(id=book.id, book=book, owns=["f1"], dir_id="d1")
    assert node.book.id == book.id and node.owns == ["f1"]


def test_directory_node_holds_children(tmp_path):
    d = DirectoryNode(path=tmp_path / "Dune", child_files=["f1"], books=["b1"])
    assert d.id == DirectoryNode.id_for(tmp_path / "Dune")
    assert d.child_files == ["f1"] and d.books == ["b1"]


def test_leaf_id_for_whole_folder_equals_node_id(tmp_path):
    from colophon.core.graph import _node_id, leaf_id_for

    folder = tmp_path / "Dune"
    # No files (or files covering the whole folder) → the folder's own id.
    assert leaf_id_for(folder, []) == _node_id(folder)


def test_leaf_id_for_subset_is_distinct_and_stable(tmp_path):
    from colophon.core.graph import _node_id, leaf_id_for

    folder = tmp_path / "Brandon Sanderson"
    legion = folder / "Legion.mp3"
    elantris = folder / "Elantris.mp3"

    a = leaf_id_for(folder, [legion])
    b = leaf_id_for(folder, [elantris])

    assert a != b                      # different subsets → different ids
    assert a != _node_id(folder)       # a subset is never the folder id
    assert a == leaf_id_for(folder, [legion])           # stable
    assert b == leaf_id_for(folder, [elantris, elantris])  # order/dupes don't matter


def test_directory_node_carries_inferred_author(tmp_path):
    d = DirectoryNode(path=tmp_path / "Stephen King", kind="author", author="Stephen King")
    assert d.kind == "author" and d.author == "Stephen King"
    assert DirectoryNode(path=tmp_path / "x").author is None  # default
