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
from colophon.core.normalize import normalize_text
from colophon.core.pathscheme import sanitize_segment


class FosterResult(_Base):
    """Outcome of fostering one file. `destination`/`error` are mutually exclusive."""

    source: Path
    destination: Path | None = None
    ok: bool
    error: str | None = None


class RestructureResult(_Base):
    """Outcome of restructuring loose files into books."""

    fostered: int = 0  # files successfully moved into their own book dir
    retagged: int = 0  # files whose tags were written (0 when write_tags is off)
    failures: list[FosterResult] = []  # noqa: RUF012 - pydantic default, copied per instance
    book_ids: list[str] = []  # noqa: RUF012 - pydantic default, copied per instance
    batch_id: str = ""  # operations-log batch for this foster (audit / future undo)


def derive_book_fields(destination: Path, author_override: str | None) -> tuple[str, str]:
    """(author, title) for a fostered file at `destination`, e.g.
    `/root/Shiloh Walker/Burning Up/Burning Up.mp3`:
    author = `author_override` or the original parent folder name
    (`destination.parent.parent.name`); title = `normalize_text` of the
    stem-named book dir (`destination.parent.name`)."""
    author = author_override or destination.parent.parent.name
    title = normalize_text(destination.parent.name)
    return author, title


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


def foster_work(files: list[Path], parent: Path, subdir_name: str) -> list[Path]:
    """Move `files` into a new `parent/<sanitized subdir_name>/` directory and
    return their new paths. The directory must not already exist, so an existing
    book is never silently merged into (raises FileExistsError). Because the
    target dir is created fresh, a mid-loop failure cannot overwrite an existing
    file; already-moved files are left in the new dir (no per-file rollback)."""
    target_dir = parent / sanitize_segment(subdir_name)
    if target_dir.exists():
        raise FileExistsError(f"{target_dir} already exists")
    target_dir.mkdir()
    destinations: list[Path] = []
    for path in files:
        dest = target_dir / path.name
        path.rename(dest)
        destinations.append(dest)
    return destinations
