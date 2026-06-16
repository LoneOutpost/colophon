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
