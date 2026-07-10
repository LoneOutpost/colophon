from colophon.adapters.repository.store import HistoryRepo, OperationRepo, connect, migrate
from colophon.core.models import EditChange, OperationRecord


def _repos(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return HistoryRepo(conn), OperationRepo(conn)


def test_history_reassign_book_rekeys_rows(tmp_path):
    hist, _ = _repos(tmp_path)
    hist.record("b1", [EditChange(book_id="old", field="title", old_value="a", new_value="b")])
    hist.reassign_book("old", "new")
    assert hist.list_batch("b1")[0].book_id == "new"


def test_operations_reassign_book_rekeys_rows(tmp_path):
    _, ops = _repos(tmp_path)
    ops.record(OperationRecord(batch_id="b2", book_id="old", op_type="x", target="t", outcome="ok"))
    ops.reassign_book("old", "new")
    assert ops.list_batch("b2")[0].book_id == "new"
