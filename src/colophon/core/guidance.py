"""Pure mapping from a finding (or a weak-identity review reason) to a plain-language
suggestion and the set of next-actions that resolve it. UI-agnostic and unit-tested:
`state_panel` renders these and wires each FixAction to a real behavior. Actions reuse
existing surfaces (Acquire, Persist, Matches, Files, Re-probe, Acknowledge) — no new
remedy operations here."""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from colophon.core.models import FindingCode


class FixAction(StrEnum):
    ACQUIRE = "acquire"          # browse a connected store for a good copy of the file
    REPROBE = "reprobe"          # re-read this book's file durations, re-check the flag
    ORGANIZE = "organize"        # Persist > Organize (gated by blocking errors)
    FILES = "files"              # jump to the Files list to remove a duplicate
    MATCHES = "matches"          # find a source match to confirm identity
    ACKNOWLEDGE = "acknowledge"  # dismiss an advisory finding
    DELETE = "delete"            # permanently delete corrupt files / a missing book


class Guidance(NamedTuple):
    suggestion: str
    actions: tuple[FixAction, ...]


# Acquire is a convenience, not the mandate: the real fix (a correct copy of the file,
# from wherever it lives) leads; the store browse is offered second.
_CORRUPT = Guidance(
    "This file is corrupt or incomplete. The real fix is to replace it with a good copy "
    "of the file, from wherever you have it. If the file lives on a connected store, you "
    "can browse for it in Acquire. If you just want it gone, delete it from disk.",
    (FixAction.ACQUIRE, FixAction.REPROBE, FixAction.DELETE),
)
_MIXED = Guidance(
    "This folder holds more than one book. Persist, then Organize, files each one to its "
    "own home. Organize stays blocked until any file error is fixed. If the split already "
    "looks right (each book is its own file), dismiss this note.",
    (FixAction.ORGANIZE, FixAction.ACKNOWLEDGE),
)
_DUP = Guidance(
    "The same book is here more than once. Keep one and remove the extra from the Files "
    "list.",
    (FixAction.FILES, FixAction.ACKNOWLEDGE),
)
_UNCLEAR = Guidance(
    "The folder layout could not be worked out. Check it manually, then dismiss this note.",
    (FixAction.ACKNOWLEDGE,),
)
_MIXED_QUALITY = Guidance(
    "This book's files vary in audio quality (bitrate or format), which can mean two different "
    "editions were grouped as one. Check the Files list, split it if these are separate editions, "
    "or dismiss this note if the mix is intentional.",
    (FixAction.FILES, FixAction.ACKNOWLEDGE),
)

_BY_CODE: dict[FindingCode, Guidance] = {
    FindingCode.EMPTY_AUDIO: _CORRUPT,
    FindingCode.MIXED_WORKS: _MIXED,
    FindingCode.MULTI_IN_AUTHOR: _MIXED,
    FindingCode.MULTI_IN_UNDETERMINED: _MIXED,
    FindingCode.LOOSE_IN_AUTHOR: _MIXED,
    FindingCode.DUP_FORMAT: _DUP,
    FindingCode.DUP_EDITION: _DUP,
    FindingCode.STRUCTURE_UNCLEAR: _UNCLEAR,
    FindingCode.MIXED_QUALITY: _MIXED_QUALITY,
}


def finding_guidance(code: FindingCode) -> Guidance:
    """The suggestion + next-actions for a finding code. Unknown codes fall back to a
    manual acknowledge, so a new finding is never actionless."""
    return _BY_CODE.get(code, _UNCLEAR)


def review_guidance() -> Guidance:
    """Guidance for a weak-identity review reason (identity is only a guess): find a match."""
    return Guidance(
        "This identity is only a guess, from the filename or folder. Find a source match "
        "to confirm it.",
        (FixAction.MATCHES,),
    )
