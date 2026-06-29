"""Integration tests for owned-file overlap re-association (#166).

A multi-book leaf's id is a hash of its folder + sorted owned filenames, so when its
file set churns the id changes. Without re-association, ``commit_scan(reconcile=True)``
prunes the old row and the book's app state (cover, confidence, manual edits) is lost.
These tests drive the real scan path and assert that a re-scanned book adopts its prior
record's durable id and state by owned-file overlap.

Fixtures use the ffmpeg-backed ``make_audio`` factory and embed an ``album`` tag so a
folder's files group into multi-track works (``group_works``), yielding a MULTI folder
with leaves that own more than one file — the only structural shape from which a track
can be added/removed while keeping overlap with the prior leaf.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookUnit, Provenance
from colophon.services.ingest import ScanPlan, commit_scan, plan_scan_graph

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
_TEMPLATE = "$Title"
_SCHEME = "$Author/$Title"


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _album_track(path: Path, album: str) -> None:
    """Write a 1s silent track carrying an ``album`` tag so files grouped by album form
    a multi-track work. Skips the test if ffmpeg is absent."""
    if not _HAVE_FFMPEG:
        pytest.skip("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "1",
            "-metadata", f"album={album}", "-metadata", "artist=Author",
            str(path),
        ],
        check=True,
    )


def _scan(repo: BookUnitRepo, root: Path) -> ScanPlan:
    """Run the real scan path: plan via the entity graph, then commit with reconcile."""
    plan = plan_scan_graph(repo, root, template=_TEMPLATE, directory_scheme=_SCHEME)
    commit_scan(repo, plan, reconcile=True)
    return plan


def _leaf_titled(repo: BookUnitRepo, folder: Path, title: str) -> BookUnit:
    book = next((b for b in repo.list_all() if b.source_folder == folder and b.title == title), None)
    assert book is not None, f"no persisted leaf titled {title!r} in {folder}"
    return book


def test_leaf_file_churn_keeps_id_and_state(tmp_path: Path) -> None:
    """A MULTI leaf that loses a track keeps its durable id and app state (real path)."""
    repo = _repo(tmp_path)
    author = tmp_path / "Author"
    _album_track(author / "e01.mp3", "Elantris")
    _album_track(author / "e02.mp3", "Elantris")
    _album_track(author / "l01.mp3", "Legion")
    _album_track(author / "l02.mp3", "Legion")
    _scan(repo, author.parent)

    assert len(repo.ids_in_folder(author)) > 1, "fixture must split into >1 persisted book"

    leaf = _leaf_titled(repo, author, "Elantris")
    original_id = leaf.id
    leaf.cover_path = Path("/x.jpg")
    leaf.confidence = 88.0
    leaf.manually_confirmed = True
    repo.upsert(leaf)

    # Churn: drop one of Elantris's two tracks. The leaf id (hash of folder + filenames)
    # changes, but it still shares e01 with the prior record.
    (author / "e02.mp3").unlink()
    _scan(repo, author.parent)

    survivor = repo.get(original_id)
    assert survivor is not None, "original leaf id was pruned (state lost)"
    assert survivor.cover_path == Path("/x.jpg")
    assert survivor.confidence == 88.0
    assert survivor.manually_confirmed is True
    assert [sf.path.name for sf in survivor.source_files] == ["e01.mp3"]


def test_single_to_multi_carries_identity_to_dominant_heir(tmp_path: Path) -> None:
    """A SINGLE book that restructures into MULTI hands its id+state to the highest-
    owned-file-overlap leaf (real path)."""
    repo = _repo(tmp_path)
    author = tmp_path / "Author"
    _album_track(author / "e01.mp3", "Elantris")
    _album_track(author / "e02.mp3", "Elantris")
    _scan(repo, author.parent)

    assert repo.ids_in_folder(author) == {BookUnit.id_for(author)}, "fixture must start SINGLE"
    book = next(b for b in repo.list_all() if b.source_folder == author)
    original_id = book.id
    book.cover_path = Path("/x.jpg")
    book.confidence = 77.0
    book.manually_confirmed = True
    repo.upsert(book)

    # Add a second album so the folder now clusters MULTI; Elantris (e01+e02) overlaps
    # the original single fully and is the dominant heir.
    _album_track(author / "l01.mp3", "Legion")
    _album_track(author / "l02.mp3", "Legion")
    _scan(repo, author.parent)

    assert len(repo.ids_in_folder(author)) > 1
    heir = repo.get(original_id)
    assert heir is not None, "original id did not survive into the dominant heir"
    assert {sf.path.name for sf in heir.source_files} == {"e01.mp3", "e02.mp3"}
    assert heir.cover_path == Path("/x.jpg")
    assert heir.confidence == 77.0
    assert heir.manually_confirmed is True


def test_multi_to_single_carries_identity(tmp_path: Path) -> None:
    """A MULTI folder collapsing to one book hands the surviving single its highest-
    overlap leaf's id+state (real path)."""
    repo = _repo(tmp_path)
    author = tmp_path / "Author"
    _album_track(author / "e01.mp3", "Elantris")
    _album_track(author / "e02.mp3", "Elantris")
    _album_track(author / "l01.mp3", "Legion")
    _album_track(author / "l02.mp3", "Legion")
    _scan(repo, author.parent)

    assert len(repo.ids_in_folder(author)) > 1, "fixture must start MULTI"
    leaf = _leaf_titled(repo, author, "Elantris")
    original_id = leaf.id
    leaf.cover_path = Path("/x.jpg")
    leaf.confidence = 66.0
    leaf.manually_confirmed = True
    repo.upsert(leaf)

    # Remove Legion entirely; the folder collapses to one SINGLE book owning e01+e02,
    # which fully overlaps the prior Elantris leaf.
    (author / "l01.mp3").unlink()
    (author / "l02.mp3").unlink()
    _scan(repo, author.parent)

    assert len(repo.ids_in_folder(author)) == 1
    survivor = repo.get(original_id)
    assert survivor is not None, "surviving single did not inherit the leaf's id"
    assert {sf.path.name for sf in survivor.source_files} == {"e01.mp3", "e02.mp3"}
    assert survivor.cover_path == Path("/x.jpg")
    assert survivor.confidence == 66.0
    assert survivor.manually_confirmed is True


def test_manual_identity_edits_survive_multi_to_single(tmp_path: Path) -> None:
    """A MULTI->SINGLE collapse must not silently wipe hand-curated identity. The
    surviving single re-derives its identity but keeps every MANUAL-provenance edit
    (and its provenance) from the heir leaf."""
    repo = _repo(tmp_path)
    author = tmp_path / "Author"
    _album_track(author / "e01.mp3", "Elantris")
    _album_track(author / "e02.mp3", "Elantris")
    _album_track(author / "l01.mp3", "Legion")
    _album_track(author / "l02.mp3", "Legion")
    _scan(repo, author.parent)

    leaf = _leaf_titled(repo, author, "Elantris")
    original_id = leaf.id
    leaf.description = "Hand-written blurb"
    leaf.provenance["description"] = Provenance.MANUAL.value
    leaf.asin = "B00MANUAL1"
    leaf.provenance["asin"] = Provenance.MANUAL.value
    repo.upsert(leaf)

    (author / "l01.mp3").unlink()
    (author / "l02.mp3").unlink()
    _scan(repo, author.parent)

    survivor = repo.get(original_id)
    assert survivor is not None
    assert survivor.description == "Hand-written blurb"  # manual edit survives the collapse
    assert survivor.provenance.get("description") == Provenance.MANUAL.value
    assert survivor.asin == "B00MANUAL1"
    assert survivor.provenance.get("asin") == Provenance.MANUAL.value


def test_new_leaf_fresh_id_and_merged_away_book_pruned(tmp_path: Path) -> None:
    """A genuinely new (disjoint) leaf gets a fresh id; a persisted book that no
    projected unit claims is pruned by ``commit_scan(reconcile=True)`` (real path)."""
    repo = _repo(tmp_path)
    author = tmp_path / "Author"
    _album_track(author / "e01.mp3", "Elantris")
    _album_track(author / "e02.mp3", "Elantris")
    _album_track(author / "l01.mp3", "Legion")
    _album_track(author / "l02.mp3", "Legion")
    _scan(repo, author.parent)

    legion = _leaf_titled(repo, author, "Legion")
    legion_id = legion.id
    elantris_id = _leaf_titled(repo, author, "Elantris").id

    # Delete Legion's tracks (no overlap survives -> pruned) and add a disjoint new
    # book (Mistborn) that shares no file with any prior leaf -> a fresh id.
    (author / "l01.mp3").unlink()
    (author / "l02.mp3").unlink()
    _album_track(author / "m01.mp3", "Mistborn")
    _album_track(author / "m02.mp3", "Mistborn")
    _scan(repo, author.parent)

    assert repo.get(legion_id) is None, "merged-away book should be pruned"
    assert repo.get(elantris_id) is not None, "untouched leaf should keep its id"
    mistborn = _leaf_titled(repo, author, "Mistborn")
    assert mistborn.id not in {legion_id, elantris_id}, "new disjoint leaf must get a fresh id"
