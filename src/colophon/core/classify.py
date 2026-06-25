"""Pure structural classifier for a scanned folder.

Fed the grouped files plus their embedded tags, it determines how many distinct
works the folder holds (content axis) and what the folder represents (folder
axis), and emits findings. No I/O of its own: callers read tags and hand them in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern

from colophon.core.dirinfer import infer_from_path
from colophon.core.filename_parser import parse_filename
from colophon.core.models import (
    ConfidenceSignal,
    ContentKind,
    DetectedWork,
    EmbeddedTags,
    Finding,
    FolderKind,
)


@dataclass(frozen=True)
class FileFeatures:
    """Per-file signals the classifier reasons over."""

    path: Path
    ext: str
    duration_seconds: float
    tags: EmbeddedTags  # embedded tags read from the file


@dataclass(frozen=True)
class ClassificationResult:
    content_kind: ContentKind
    folder_kind: FolderKind
    confidence: float
    signals: list[ConfidenceSignal] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    detected_works: list[DetectedWork] = field(default_factory=list)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold() if s else ""


def _signal(name: str, points: int, detail: str) -> ConfidenceSignal:
    return ConfidenceSignal(name=name, points=points, detail=detail)


def classify_folder_kind(
    folder: Path,
    root: Path,
    features: list[FileFeatures],
    *,
    template_pattern: Pattern[str],
    scheme_patterns: list[Pattern[str]],
) -> tuple[FolderKind, list[ConfidenceSignal]]:
    """Decide author/title/undetermined via the cascade: (1) folder name vs
    embedded artist/album/title; (2) folder name vs template/dir-scheme parse;
    (3) folder name vs a file stem; (4) undetermined."""
    name = _norm(folder.name)

    artists = {_norm(f.tags.artist) for f in features if f.tags.artist}
    albums = {_norm(f.tags.album) for f in features if f.tags.album}
    titles = {_norm(f.tags.title) for f in features if f.tags.title}
    if name and name in artists:
        return FolderKind.AUTHOR, [_signal("foldername_is_artist", 3, "folder name matches embedded artist")]
    if name and (name in albums or name in titles):
        return FolderKind.TITLE, [_signal("foldername_is_album", 3, "folder name matches embedded album/title")]

    inferred = infer_from_path(folder, root, scheme_patterns)
    fn_fields = parse_filename(template_pattern, features[0].path.name) or {}
    if name and (_norm(inferred.get("author")) == name or _norm(fn_fields.get("author")) == name):
        return FolderKind.AUTHOR, [_signal("foldername_is_parsed_author", 2, "folder name matches parsed author")]
    if name and (_norm(inferred.get("title")) == name or _norm(fn_fields.get("title")) == name):
        return FolderKind.TITLE, [_signal("foldername_is_parsed_title", 2, "folder name matches parsed title")]

    if name and any(_norm(f.path.stem) == name for f in features):
        return FolderKind.TITLE, [_signal("foldername_is_file_stem", 1, "folder name matches a file stem")]

    return FolderKind.UNDETERMINED, []
