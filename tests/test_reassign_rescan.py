from pathlib import Path

from mutagen.id3 import ID3, TALB, TPE1

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import plan_rescan_folders


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _tagged(path: Path, artist: str, album: str) -> None:
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TALB(encoding=3, text=[album]))
    tags.save(path)


def test_partition_overrides_clustering_in_scan(tmp_path):
    # Three files with three distinct albums would normally cluster into three books. A stored
    # partition of {01,02} + {03} must override that and yield exactly two books.
    ingest = tmp_path / "ingest"
    folder = ingest / "Author"
    folder.mkdir(parents=True)
    _tagged(folder / "01.mp3", "Author", "Alpha")
    _tagged(folder / "02.mp3", "Author", "Beta")
    _tagged(folder / "03.mp3", "Author", "Gamma")

    plan = plan_rescan_folders(
        _repo(tmp_path), [folder], template="$Author - $Title",
        inference_root_for=lambda f: ingest,
        partitioned_folders={str(folder): [["01.mp3", "02.mp3"], ["03.mp3"]]},
    )

    groups = {frozenset(sf.path.name for sf in b.source_files) for b in plan.units}
    assert groups == {frozenset({"01.mp3", "02.mp3"}), frozenset({"03.mp3"})}
