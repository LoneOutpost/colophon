from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import state_of
from colophon.services.ingest import (
    ScanOptions,
    ScanScope,
    commit_scan,
    plan_scan,
)


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _folder(tmp_path, author, title):
    d = tmp_path / author / title
    d.mkdir(parents=True)
    (d / f"{title}.mp3").write_bytes(b"")
    return d


def test_new_only_adds_new_skips_known(tmp_path):
    repo = _repo(tmp_path)
    a = _folder(tmp_path, "Author A", "Book A")
    commit_scan(repo, plan_scan(repo, tmp_path, template="$Title"))   # legacy scan ingests A
    assert repo.get(BookUnit.id_for(a)) is not None

    b = _folder(tmp_path, "Author B", "Book B")                       # new on disk
    plan = plan_scan(repo, tmp_path, template="$Title",
                     options=ScanOptions(scope=ScanScope.NEW_ONLY))
    folders = {u.source_folder for u in plan.units}
    assert b in folders and a not in folders
    assert plan.new_books == 1


def test_new_only_honors_phase_subset(tmp_path):
    repo = _repo(tmp_path)
    _folder(tmp_path, "Author", "Book")
    plan = plan_scan(repo, tmp_path, template="$Title",
                     options=ScanOptions(scope=ScanScope.NEW_ONLY,
                                         phases=frozenset({Phase.SEARCH, Phase.CATEGORIZE})))
    book = plan.units[0]
    assert state_of(book, Phase.SEARCH) is PhaseState.FRESH
    assert state_of(book, Phase.CATEGORIZE) is PhaseState.FRESH
    assert state_of(book, Phase.IDENTIFY) is PhaseState.PENDING


def test_options_none_is_legacy_behavior(tmp_path):
    repo = _repo(tmp_path)
    _folder(tmp_path, "Author", "Book")
    plan = plan_scan(repo, tmp_path, template="$Title")   # no options -> legacy: runs all phases
    book = plan.units[0]
    assert state_of(book, Phase.IDENTIFY) is PhaseState.FRESH
