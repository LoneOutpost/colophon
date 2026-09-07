"""Recognise a folder that holds one disc of a book split across CDs.

A book ripped disc-by-disc lands as `Book/CD01`, `Book/CD02`, … or
`Book/Title - Disk1`, `Book/Title - Disk2`, …. The scanner groups per directory, so each disc
became its own book — ten books for one novel, each titled with whatever the disc folder name
happened to yield.

The match is ANCHORED: the disc token must be the whole name or a trailing segment. This library
contains `Discord's Apple` and `Lords of Discipline`, both of which a naive substring match hits.
"""

from __future__ import annotations

import re

# <separator or start> cd|disc|disk|dvd <optional separator> <1-3 digits> [of N] <trailing junk> END
_DISC = re.compile(
    r"(?:^|[\s._\-\[(])"        # start of name, or a separator before the token
    r"(?:cd|disc|disk|dvd)"     # the marker word
    r"[\s._\-]?"                # optional separator between marker and number
    r"(\d{1,3})"                # the disc number
    r"(?:\s*of\s*\d{1,3})?"     # an optional "of N"
    r"[\s._\-\])]*$",           # only closing punctuation may follow
    re.IGNORECASE,
)


def disc_number(folder_name: str) -> int | None:
    """The disc number a folder name denotes, or None when it does not denote one."""
    match = _DISC.search(folder_name)
    return int(match.group(1)) if match else None
