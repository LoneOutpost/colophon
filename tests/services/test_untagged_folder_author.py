"""End-to-end: untagged author folders resolve to the folder-name author on a fresh scan AND
self-heal on a REFRESH after a bad datafile sidecar is removed. Real TE_Audiobooks_S shape, no network."""

from __future__ import annotations

import json
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import (
    ScanOptions,
    ScanScope,
    commit_scan,
    plan_scan_graph,
)


def _build(root: Path, tree: dict[str, list[str]], *, datafile: dict | None = None) -> None:
    for author, files in tree.items():
        d = root / author
        d.mkdir(parents=True)
        for f in files:
            (d / f).write_bytes(b"")
        if datafile is not None:
            (d / "metadata.json").write_text(json.dumps(datafile), encoding="utf-8")


def _authors_by_folder(repo: BookUnitRepo) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for b in repo.list_all():
        out.setdefault(b.source_folder.name, set()).update(b.authors or ["(none)"])
    return out


def test_fresh_scan_assigns_folder_name_author(tmp_path):
    root = tmp_path / "TE_Audiobooks_S"
    _build(root, {
        "stella Rimington": ["Close Call (Liz Carlyle 8).mp3", "Secret Asset (Liz Carlyle 2).mp3"],
        "Sarah Graves": ["Dead Cat Bounce (Home Repair is Homicide 1).mp3",
                         "Winter at the Door (Lizzie Snow 1).mp3"],
    })
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    repo = BookUnitRepo(conn)
    plan = plan_scan_graph(repo, root, template="$Author - $Title", directory_scheme="")
    commit_scan(repo, plan, reconcile=True)
    by_folder = _authors_by_folder(repo)
    conn.close()

    assert by_folder["stella Rimington"] == {"stella Rimington"}
    assert by_folder["Sarah Graves"] == {"Sarah Graves"}


def test_refresh_heals_stranded_author_after_bad_datafile_removed(tmp_path):
    tree = {"stella Rimington": ["Close Call (Liz Carlyle 8).mp3",
                                 "Secret Asset (Liz Carlyle 2).mp3"]}
    root = tmp_path / "TE_Audiobooks_S"
    # title != folder name, authors == bucket -> NOT vetted as a container datafile -> it leaks.
    _build(root, tree, datafile={"title": "Placeholder Collection", "authors": ["TE_Audiobooks_S"]})

    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    repo = BookUnitRepo(conn)

    # Phase 1: the bad datafile sidecar leaks the bucket as author.
    plan = plan_scan_graph(repo, root, template="$Author - $Title", directory_scheme="")
    commit_scan(repo, plan, reconcile=True)
    assert _authors_by_folder(repo)["stella Rimington"] == {"TE_Audiobooks_S"}

    # Operator removes the erroneous datafile sidecar.
    (root / "stella Rimington" / "metadata.json").unlink()

    # Phase 2: REFRESH now heals to the folder-name author (was previously stranded to (none)).
    opts = ScanOptions(scope=ScanScope.REFRESH)
    plan2 = plan_scan_graph(repo, root, template="$Author - $Title",
                            directory_scheme="", options=opts)
    commit_scan(repo, plan2, reconcile=True)
    healed = _authors_by_folder(repo)["stella Rimington"]
    conn.close()

    assert healed == {"stella Rimington"}
