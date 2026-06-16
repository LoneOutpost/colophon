"""Ingest service: scan a directory into persisted BookUnit candidates."""

from __future__ import annotations

from pathlib import Path

from colophon.adapters.audio import probe_audio_file
from colophon.adapters.repository.store import BookUnitRepo
from colophon.adapters.scan import group_book_units
from colophon.adapters.sidecar import read_sidecar
from colophon.adapters.tags import read_embedded_tags
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.models import BookUnit
from colophon.core.reconcile import reconcile


def scan_ingest(repo: BookUnitRepo, root: Path, *, template: str) -> list[BookUnit]:
    """Scan `root`, build a BookUnit per folder, reconcile evidence, and persist.

    Embedded tags are read from the first audio file in each unit; the filename
    template is applied to that same file. Returns the persisted units.
    """
    pattern = compile_template(template)
    results: list[BookUnit] = []
    for unit in group_book_units(root):
        book = BookUnit.new(source_folder=unit.folder)
        book.source_files = [probe_audio_file(p) for p in unit.files]

        first = unit.files[0]
        embedded = read_embedded_tags(first)
        filename_fields = parse_filename(pattern, first.name) or {}

        sidecar = read_sidecar(unit.folder)
        reconcile(
            book,
            embedded=embedded,
            sidecar=sidecar,
            dir_title=unit.folder.name,
            filename_fields=filename_fields,
        )
        repo.upsert(book)
        results.append(book)
    return results
