"""Pure mapping from a finding (or a weak-identity review reason) to a plain-language
suggestion and the set of next-actions that resolve it. UI-agnostic and unit-tested:
`state_panel` renders these and wires each FixAction to a real behavior. Actions reuse
existing surfaces (Persist, Matches, Files, Re-probe, Acknowledge) — no new
remedy operations here."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, NamedTuple

from colophon.core.models import Finding, FindingCode


class FixAction(StrEnum):
    REPROBE = "reprobe"          # re-read this book's file durations, re-check the flag
    ORGANIZE = "organize"        # Persist > Organize (gated by blocking errors)
    FILES = "files"              # jump to the Files list to remove a duplicate
    MATCHES = "matches"          # find a source match to confirm identity
    ACKNOWLEDGE = "acknowledge"  # dismiss an advisory finding
    DELETE = "delete"            # permanently delete corrupt files / a missing book
    FIX_EXTENSION = "fix_extension"  # rename mislabeled files to their true extension
    USE_SUGGESTED = "use_suggested"  # write a conflict finding's folder value into its book field


class Guidance(NamedTuple):
    suggestion: str
    actions: tuple[FixAction, ...]


_CORRUPT = Guidance(
    "This file is corrupt or incomplete. The real fix is to replace it with a good copy "
    "of the file, from wherever you have it. If you just want it gone, delete it from disk.",
    (FixAction.REPROBE, FixAction.DELETE),
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
_MISSING_TRACKS = Guidance(
    "This book looks like it's missing one or more tracks from its sequence. Add the missing "
    "files, or dismiss this note if the book is intentionally partial.",
    (FixAction.ACKNOWLEDGE,),
)
_METADATA_CONFLICT = Guidance(
    "This book's embedded tags name a different book than its folder does (a bulk tagger wrote the "
    "wrong metadata). Check the folder against the tags, fix the wrong fields, or dismiss this note "
    "if the tags are actually right.",
    (FixAction.MATCHES, FixAction.ACKNOWLEDGE),
)
_EXTENSION_MISMATCH = Guidance(
    "One or more of this book's files has an extension that does not match its real audio format "
    "(e.g. an MP3 named .opus). Rename them to the correct extension, or dismiss this note.",
    (FixAction.FIX_EXTENSION, FixAction.ACKNOWLEDGE),
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
    FindingCode.MISSING_TRACKS: _MISSING_TRACKS,
    FindingCode.METADATA_CONFLICT: _METADATA_CONFLICT,
    FindingCode.EXTENSION_MISMATCH: _EXTENSION_MISMATCH,
}


def finding_guidance(code: FindingCode) -> Guidance:
    """The suggestion + next-actions for a finding code. Unknown codes fall back to a
    manual acknowledge, so a new finding is never actionless."""
    return _BY_CODE.get(code, _UNCLEAR)


# The detail prefix node_classify's author-vs-folder check writes (`author: tag 'X' vs folder 'Y'`).
# The other two METADATA_CONFLICT producers (title corroboration, album-vs-folder) are about the book.
# Public: node_classify raises and retracts this finding with the same constant.
AUTHOR_CONFLICT_PREFIX = "author:"
_OWN_FOLDER_CODES = frozenset({
    FindingCode.MIXED_WORKS, FindingCode.MULTI_IN_AUTHOR,
    FindingCode.MULTI_IN_UNDETERMINED, FindingCode.STRUCTURE_UNCLEAR,
})

# The detail prefix node_classify's title-corroboration check writes (`metadata title "X" vs
# folder "Y"`). Public: node_classify's own METADATA_CONFLICT-retraction check shares this constant
# so the two agree on what "mine" means without duplicating the literal.
TITLE_CONFLICT_PREFIX = 'metadata title "'

# The detail prefix node_classify's folder-name check writes (`folder name: folder 'X' vs tags 'Y'`):
# an author folder whose own name shares nothing with the author its books' tags elected for it.
# Public: node_classify raises/retracts it and the controller syncs it with the same constant.
FOLDER_NAME_CONFLICT_PREFIX = "folder name:"

FindingScope = Literal["author_folder", "own_folder", "book"]


def finding_scope(finding: Finding) -> FindingScope:
    """What a finding is about, so the review queue can group it by its probable cause.

    Decided per finding, not per code: METADATA_CONFLICT is raised by several unrelated checks, and
    only the author and folder-name ones question a folder's claim. Structure findings are about how
    the book's own folder is laid out, so books clustered in one folder group together. Anything
    else, including a code added later, is about the book alone.
    """
    if finding.code == FindingCode.METADATA_CONFLICT and (finding.detail or "").startswith(
            (AUTHOR_CONFLICT_PREFIX, FOLDER_NAME_CONFLICT_PREFIX)):
        return "author_folder"
    if finding.code in _OWN_FOLDER_CODES:
        return "own_folder"
    return "book"


# What each finding means to a person scanning the review queue, in plain words. Shared with
# finding_scope's grouping so the two never describe the same finding two different ways.
_FINDING_PHRASE: dict[FindingCode, str] = {
    FindingCode.MIXED_QUALITY: "files differ in audio quality",
    FindingCode.MISSING_TRACKS: "tracks are missing",
    FindingCode.EXTENSION_MISMATCH: "a file's extension is wrong",
    FindingCode.MIXED_WORKS: "several works share one folder",
    FindingCode.MULTI_IN_AUTHOR: "several books share one folder",
    FindingCode.MULTI_IN_UNDETERMINED: "several books share one folder",
    FindingCode.STRUCTURE_UNCLEAR: "the folder layout is unclear",
    FindingCode.DUP_FORMAT: "the same book in two formats",
    FindingCode.DUP_EDITION: "two editions of one book",
}


def finding_phrase(finding: Finding) -> str:
    """The finding's problem in plain words, for the review queue. METADATA_CONFLICT is split by
    which check raised it, same as `finding_scope`; everything else comes from the per-code table."""
    if finding.code == FindingCode.METADATA_CONFLICT:
        if (finding.detail or "").startswith(AUTHOR_CONFLICT_PREFIX):
            return "tags name a different author"
        if (finding.detail or "").startswith(TITLE_CONFLICT_PREFIX):
            return "title disagrees with the folder"
        if (finding.detail or "").startswith(FOLDER_NAME_CONFLICT_PREFIX):
            return "the folder's name disagrees with its books' tags"
        return "tags disagree with the folder"
    return _FINDING_PHRASE.get(finding.code, "needs a look")


def review_guidance() -> Guidance:
    """Guidance for a weak-identity review reason (identity is only a guess): find a match."""
    return Guidance(
        "This identity is only a guess, from the filename or folder. Find a source match "
        "to confirm it.",
        (FixAction.MATCHES,),
    )
