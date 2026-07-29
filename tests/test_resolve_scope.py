from pathlib import Path

from colophon.core.models import SourceFile
from colophon.services.resolve import FileChanges, compute_file_changes


def _sf(name: str, size: int = 1_000_000, dur: float = 60.0) -> SourceFile:
    return SourceFile(path=Path("/lib/bk") / name, size=size, duration_seconds=dur, ext="mp3")


def test_diff_added_and_removed():
    prior = [_sf("01.mp3")]
    post = [_sf("01.mp3"), _sf("02.mp3")]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert [p.name for p in ch.added] == ["02.mp3"]
    assert ch.removed == []
    assert not ch.is_empty
    assert "added" in ch.summary().lower()


def test_diff_corrupt_resolved_and_newly_corrupt():
    prior = [_sf("01.mp3", size=2_000_000, dur=0.0), _sf("02.mp3", dur=60.0)]
    post = [_sf("01.mp3", size=2_000_000, dur=45.0), _sf("02.mp3", size=2_000_000, dur=0.0)]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert [p.name for p in ch.corrupt_resolved] == ["01.mp3"]
    assert [p.name for p in ch.newly_corrupt] == ["02.mp3"]


def test_diff_rename_heuristic_pairs_by_size_and_duration():
    prior = [_sf("oldname.mp3", size=5_000_000, dur=120.0)]
    post = [_sf("newname.mp3", size=5_000_000, dur=120.0)]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert ch.added == [] and ch.removed == []
    assert [(a.name, b.name) for a, b in ch.renamed] == [("oldname.mp3", "newname.mp3")]


def test_diff_missing_transitions_and_empty_summary():
    same = [_sf("01.mp3")]
    resolved = compute_file_changes(same, same, prior_missing=True, post_missing=False, book_id="b")
    assert resolved.missing_resolved == ["b"] and resolved.newly_missing == []
    gone = compute_file_changes(same, [], prior_missing=False, post_missing=True, book_id="b")
    assert gone.newly_missing == ["b"]
    noop = compute_file_changes(same, same, prior_missing=False, post_missing=False, book_id="b")
    assert noop.is_empty and noop.summary() == "No changes on disk"
