"""Foster service: promote a loose single-file book into its own directory.

When several standalone audiobooks sit loose in one folder (e.g. an author
directory holding `Mistborn.mp3`, `Legion.mp3`), the scanner groups them into a
single book because grouping is per-directory. Fostering moves a chosen file
into a new child directory named after the file's stem, so it scans as its own
book. `foster_one` does the disk move for one file; the controller batches and
re-scans (see `AppController.foster_files`)."""

from __future__ import annotations

from pathlib import Path

from colophon.core.models import _Base


class FosterResult(_Base):
    """Outcome of fostering one file. `destination`/`error` are mutually exclusive."""

    source: Path
    destination: Path | None = None
    ok: bool
    error: str | None = None


def foster_one(path: Path) -> Path:
    """Move `path` into a new sibling subdirectory named after its stem.

    `/author/Mistborn.mp3` becomes `/author/Mistborn/Mistborn.mp3`. The new
    directory must not already exist, so an existing book is never silently
    merged into (raises FileExistsError). Raises FileNotFoundError if `path` is
    not a file.
    """
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    target_dir = path.parent / path.stem
    if target_dir.exists():
        raise FileExistsError(f"{target_dir} already exists")
    target_dir.mkdir()
    destination = target_dir / path.name
    path.rename(destination)
    return destination
