from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import BookUnit, ContentKind, Phase, PhaseState
from colophon.core.phases import mark, state_of
from colophon.services.ingest import run_local_phases


def _book(tmp_path):
    d = tmp_path / "Author" / "Book"
    d.mkdir(parents=True)
    (d / "Book.mp3").write_bytes(b"")
    return d, BookUnit.new(source_folder=d)


def _args(tmp_path):
    return dict(root=tmp_path, pattern=compile_template("$Title"), scheme=parse_scheme(""))


def test_runs_only_selected_phases(tmp_path):
    d, book = _book(tmp_path)
    run_local_phases(book, frozenset({Phase.SEARCH}), force=False,
                     unit_files=[d / "Book.mp3"], **_args(tmp_path))
    assert state_of(book, Phase.SEARCH) is PhaseState.FRESH
    assert state_of(book, Phase.CATEGORIZE) is PhaseState.PENDING
    assert state_of(book, Phase.IDENTIFY) is PhaseState.PENDING


def test_skips_fresh_unless_forced(tmp_path):
    d, book = _book(tmp_path)
    run_local_phases(book, frozenset({Phase.SEARCH}), force=False,
                     unit_files=[d / "Book.mp3"], **_args(tmp_path))
    book.content_kind = ContentKind.MULTI          # a value classify won't produce for a 1-file folder
    mark(book, Phase.CATEGORIZE, PhaseState.FRESH)

    run_local_phases(book, frozenset({Phase.CATEGORIZE}), force=False, **_args(tmp_path))
    assert book.content_kind is ContentKind.MULTI   # FRESH -> skipped, unchanged

    run_local_phases(book, frozenset({Phase.CATEGORIZE}), force=True, **_args(tmp_path))
    assert book.content_kind is not ContentKind.MULTI  # forced -> re-classified
