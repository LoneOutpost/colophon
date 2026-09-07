"""Walk a directory tree and group audio files into book units.

A book unit = one directory that directly contains audio files (ported grouping
rule from id3editor's library.py, generalized to all audio extensions).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from colophon.adapters.audio import is_audio_file
from colophon.core.disc_folder import disc_number


@dataclass
class BookUnitFiles:
    folder: Path
    files: list[Path]


def _natural_key(path: Path) -> list[object]:
    # split into digit / non-digit runs so "2" sorts before "10"
    return [
        int(token) if token.isdigit() else token.lower()
        for token in re.split(r"(\d+)", path.name)
    ]


def _members_by_parent(units: list[BookUnitFiles]) -> dict[Path, list[BookUnitFiles]]:
    """Group units by their parent folder."""
    by_parent: dict[Path, list[BookUnitFiles]] = {}
    for unit in units:
        by_parent.setdefault(unit.folder.parent, []).append(unit)
    return by_parent


def collapse_child_folders(
    units: list[BookUnitFiles],
    *,
    combined: dict[str, frozenset[str]] | None = None,
) -> list[BookUnitFiles]:
    """Fold a parent's child folders into one unit, for books split across directories.

    Two triggers, one mechanism:

    * the disc heuristic — every audio-bearing child of a parent names a disc (`CD01`,
      `Title - Disk2`), and the parent holds no audio of its own. Those were never separate books;
      the scanner mis-saw one book as N.
    * `combined` — a parent mapped to the specific child folders a user combined. Membership is
      explicit, never "everything under the parent": combining two books under an author folder must
      not swallow every other book by that author on the next rescan.

    Files are emitted in (disc number, natural name) order, so CD2 precedes CD10 and the result
    feeds `resolve_part_order` unchanged. A collapsed parent's own identity comes from its folder
    name, which is where the title lives.
    """
    combined = combined or {}
    own_audio = {u.folder for u in units}
    out: list[BookUnitFiles] = []
    consumed: set[Path] = set()

    for parent, children in _members_by_parent(units).items():
        chosen = combined.get(str(parent))
        if chosen is not None:
            members = [c for c in children if str(c.folder) in chosen]
            if len(members) < 2:
                continue                      # nothing to fold; children stay as they are
            ordering = {c.folder: (0, _natural_key(c.folder)) for c in members}
        else:
            if parent in own_audio or len(children) < 2:
                continue                      # loose audio beside discs, or a lone child
            numbers = {c.folder: disc_number(c.folder.name) for c in children}
            if any(n is None for n in numbers.values()):
                continue                      # a non-disc sibling means this is not a disc split
            members = children
            ordering = {c.folder: (numbers[c.folder], _natural_key(c.folder)) for c in members}

        files: list[Path] = []
        for member in sorted(members, key=lambda c: ordering[c.folder]):
            files.extend(member.files)
            consumed.add(member.folder)
        out.append(BookUnitFiles(folder=parent, files=files))

    kept = [u for u in units if u.folder not in consumed]
    return sorted([*kept, *out], key=lambda u: u.folder.name.lower())


def group_book_units(root: Path) -> list[BookUnitFiles]:
    units: list[BookUnitFiles] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        folder = Path(dirpath)
        audio = sorted(
            (folder / name for name in filenames if is_audio_file(folder / name)),
            key=_natural_key,
        )
        if not audio:
            continue
        units.append(BookUnitFiles(folder=folder, files=audio))
    units.sort(key=lambda u: u.folder.name.lower())
    return units
