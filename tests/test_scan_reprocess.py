from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, ContentKind
from colophon.services.ingest import ScanOptions, ScanScope, commit_scan, plan_scan


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _folder(root, author, title):
    d = root / author / title
    d.mkdir(parents=True)
    (d / f"{title}.mp3").write_bytes(b"")
    return d


def test_update_adds_new_books(tmp_path):
    repo = _repo(tmp_path)
    _folder(tmp_path, "Author A", "Book A")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))
    _folder(tmp_path, "Author B", "Book B")
    plan = plan_scan(repo, tmp_path, template="$Title",
                     options=ScanOptions(scope=ScanScope.UPDATE))
    assert plan.new_books == 1


def test_update_skips_fresh_known_phase(tmp_path):
    repo = _repo(tmp_path)
    a = _folder(tmp_path, "Author A", "Book A")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))
    known = repo.get(BookUnit.id_for(a))
    known.content_kind = ContentKind.MULTI
    repo.upsert(known)
    plan = plan_scan(repo, tmp_path, template="$Title",
                     options=ScanOptions(scope=ScanScope.UPDATE))
    book = next(u for u in plan.units if u.source_folder == a)
    assert book.content_kind is ContentKind.MULTI       # FRESH CATEGORIZE skipped


def test_refresh_forces_known_phase_rerun(tmp_path):
    repo = _repo(tmp_path)
    a = _folder(tmp_path, "Author A", "Book A")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))
    known = repo.get(BookUnit.id_for(a))
    known.content_kind = ContentKind.MULTI
    repo.upsert(known)
    plan = plan_scan(repo, tmp_path, template="$Title",
                     options=ScanOptions(scope=ScanScope.REFRESH))
    book = next(u for u in plan.units if u.source_folder == a)
    assert book.content_kind is not ContentKind.MULTI   # forced CATEGORIZE re-ran


def test_inference_root_calibrates_directory_inference(tmp_path):
    repo = _repo(tmp_path)
    _folder(tmp_path, "Brandon Sanderson", "Mistborn")
    sub = tmp_path / "Brandon Sanderson"
    plan = plan_scan(repo, sub, template="$Title", directory_scheme="$Author/$Title",
                     options=ScanOptions(scope=ScanScope.REFRESH), inference_root=tmp_path)
    book = plan.units[0]
    assert "Brandon Sanderson" in book.authors
