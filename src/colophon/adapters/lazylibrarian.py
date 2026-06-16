"""Read LazyLibrarian's audiobook path patterns from its config.ini.

Mirrors LL's [POSTPROCESS] keys so Colophon produces identical destinations.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from pydantic import BaseModel


class AudiobookPatterns(BaseModel):
    folder: str = "$Author/$Title"
    file: str = "$Author - $Title Part $Part of $Total"
    single_file: str = ""


def read_audiobook_patterns(config_ini: Path) -> AudiobookPatterns:
    """Read audiobook folder/file patterns from LL's config.ini.

    Returns defaults when the file or the keys are absent.
    """
    if not config_ini.exists():
        return AudiobookPatterns()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_ini)
    if not parser.has_section("POSTPROCESS"):
        return AudiobookPatterns()
    section = parser["POSTPROCESS"]
    defaults = AudiobookPatterns()
    return AudiobookPatterns(
        folder=section.get("audiobook_dest_folder", defaults.folder) or defaults.folder,
        file=section.get("audiobook_dest_file", defaults.file) or defaults.file,
        single_file=section.get("audiobook_single_file", defaults.single_file),
    )
