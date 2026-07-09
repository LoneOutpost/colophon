"""Read a LazyLibrarian folder organize pattern from its config.ini.

Only the [POSTPROCESS] folder key is imported; Colophon's own grammar owns
file/multi-part naming, so this is a one-way folder-pattern convenience, not
full LazyLibrarian parity.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from pydantic import BaseModel


class PathPatterns(BaseModel):
    folder: str = "$Author/$Title"
    single_file: str = ""


def read_audiobook_patterns(config_ini: Path) -> PathPatterns:
    """Read the audiobook folder pattern from LL's config.ini.

    Only the folder pattern is imported (its grammar is 1:1 with Colophon's);
    file/multi-part naming is Colophon's own. Returns defaults when the file or
    keys are absent.
    """
    if not config_ini.exists():
        return PathPatterns()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_ini)
    if not parser.has_section("POSTPROCESS"):
        return PathPatterns()
    section = parser["POSTPROCESS"]
    defaults = PathPatterns()
    return PathPatterns(
        folder=section.get("audiobook_dest_folder", defaults.folder) or defaults.folder,
    )
