"""Built-in franchises the classifier recognizes by default.

A folder named after a ubiquitous shared-universe franchise (e.g. `STAR WARS`) is
structurally indistinguishable from a prolific author's folder: one flat folder of many
distinct books, its name taken as the author. Recognizing these names by default lets the
classifier treat such a folder as a franchise tier instead of inventing a franchise-named
author. The list is deliberately limited to franchises whose folder name is effectively never
a real person; users declare anything else via Manage -> Franchises (see `KnownFranchiseRepo`).
"""

from __future__ import annotations

from colophon.core.graph_resolve import _name_key

# Canonical display names. Kept conservative: each is a franchise whose folder name is not a
# plausible author. Warhammer appears in several common folder spellings (they normalize to
# distinct keys), so the frequent forms are listed explicitly.
DEFAULT_FRANCHISE_NAMES = [
    "Star Wars",
    "Star Trek",
    "Doctor Who",
    "Stargate",
    "Halo",
    "Warhammer",
    "Warhammer 40,000",
    "Warhammer 40K",
    "Warhammer Horror",
    "Dungeons & Dragons",
    "Forgotten Realms",
    "Dragonlance",
    "Warcraft",
    "World of Warcraft",
    "The Witcher",
    "Mass Effect",
    "Assassin's Creed",
    "The Elder Scrolls",
]


def default_franchises() -> dict[str, str]:
    """The built-in franchises as `name_key -> display`, matching `KnownFranchiseRepo.all`'s
    shape so the two merge directly (see `KnownFranchiseRepo.active`)."""
    return {_name_key(name): name for name in DEFAULT_FRANCHISE_NAMES}
