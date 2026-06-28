
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
