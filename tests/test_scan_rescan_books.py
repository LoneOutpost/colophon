from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, ContentKind, Phase
from colophon.services.ingest import commit_scan, plan_rescan_books, plan_scan


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _folder(root, author, title):
    d = root / author / title
    d.mkdir(parents=True)
    (d / f"{title}.mp3").write_bytes(b"")
    return d


def test_rescan_books_processes_only_given_books(tmp_path):
    repo = _repo(tmp_path)
    a = _folder(tmp_path, "Author A", "Book A")
    _folder(tmp_path, "Author B", "Book B")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))
    book_a = repo.get(BookUnit.id_for(a))

    plan = plan_rescan_books(repo, [book_a], frozenset({Phase.IDENTIFY}), force=True,
                             template="$Title", directory_scheme="",
                             root_for=lambda bk: tmp_path)
    assert [u.source_folder for u in plan.units] == [a]          # only A
    assert plan.existing_books == 1


def test_rescan_books_force_reruns_fresh_phase(tmp_path):
    repo = _repo(tmp_path)
    a = _folder(tmp_path, "Author A", "Book A")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))
    book = repo.get(BookUnit.id_for(a))
    book.content_kind = ContentKind.MULTI
    repo.upsert(book)

    plan = plan_rescan_books(repo, [book], frozenset({Phase.SEARCH, Phase.CATEGORIZE}),
                             force=True, template="$Title", directory_scheme="",
                             root_for=lambda bk: tmp_path)
    assert plan.units[0].content_kind is not ContentKind.MULTI   # forced re-classify
