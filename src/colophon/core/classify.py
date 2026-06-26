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
from colophon.core.filename_cluster import cluster
from colophon.core.filename_parser import parse_filename
from colophon.core.models import (
    ConfidenceSignal,
    ContentKind,
    DetectedWork,
    EmbeddedTags,
    Finding,
    FindingCode,
    FindingSeverity,
    FolderKind,
)
from colophon.core.normalize import normalize_text


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


CONTENT_THRESHOLD = 2  # min grouping points to assert MULTI rather than UNKNOWN


def _work_key(f: FileFeatures) -> str | None:
    """The strongest available work-identity key for a file, or None."""
    t = f.tags
    if t.asin:
        return f"asin:{t.asin.strip().lower()}"
    if t.isbn:
        return f"isbn:{t.isbn.strip().lower()}"
    if t.album:
        return f"album:{_norm(t.album)}"
    return None


def _fully_untagged(features: list[FileFeatures]) -> bool:
    """True when no file carries a tag-based work key (asin/isbn/album)."""
    return all(_work_key(f) is None for f in features)


def _is_single_sequence(files: list[FileFeatures]) -> bool:
    """True when every file carries a distinct number in its stem — i.e. the
    files look like numbered parts of one work, not separate books."""
    nums: list[int] = []
    for f in files:
        m = re.search(r"\d+", f.path.stem)
        if not m:
            return False
        nums.append(int(m.group()))
    return len(set(nums)) == len(nums)


def _first(values) -> str | None:
    for v in values:
        if v:
            return v
    return None


def _to_work(group: list[FileFeatures]) -> DetectedWork:
    label = (
        _first(f.tags.album for f in group)
        or _first(f.tags.title for f in group)
        or normalize_text(group[0].path.stem)
    )
    return DetectedWork(
        label=label,
        author=_first(f.tags.artist for f in group),
        files=[f.path for f in group],
    )


def group_works(features: list[FileFeatures]) -> tuple[list[DetectedWork], list[ConfidenceSignal]]:
    """Group files into distinct works: shared asin/isbn/album group together;
    otherwise unkeyed files either form one numbered sequence or each stand alone."""
    signals: list[ConfidenceSignal] = []
    keyed: dict[str, list[FileFeatures]] = {}
    unkeyed: list[FileFeatures] = []
    for f in features:
        key = _work_key(f)
        if key is None:
            unkeyed.append(f)
        else:
            keyed.setdefault(key, []).append(f)

    works: list[list[FileFeatures]] = list(keyed.values())
    if keyed:
        signals.append(_signal("tag_work_keys", 2 * len(keyed), f"{len(keyed)} work key(s) from tags"))

    if unkeyed:
        if len(unkeyed) > 1 and _is_single_sequence(unkeyed):
            works.append(unkeyed)
            signals.append(_signal("filename_sequence", 2, f"{len(unkeyed)} files form one numbered sequence"))
        else:
            works.extend([f] for f in unkeyed)
            if len(unkeyed) > 1:
                signals.append(_signal("unkeyed_singletons", 0, f"{len(unkeyed)} files lack a shared work key"))

    return [_to_work(g) for g in works], signals


def content_kind_for(works: list[DetectedWork], signals: list[ConfidenceSignal]) -> ContentKind:
    if len(works) == 1:
        return ContentKind.SINGLE
    points = sum(s.points for s in signals)
    return ContentKind.MULTI if points >= CONTENT_THRESHOLD else ContentKind.UNKNOWN


def _actionable_finding(
    content_kind: ContentKind, folder_kind: FolderKind, works: list[DetectedWork]
) -> Finding | None:
    if content_kind is ContentKind.SINGLE and folder_kind is FolderKind.AUTHOR:
        return Finding(code=FindingCode.LOOSE_IN_AUTHOR, severity=FindingSeverity.WARN,
                       detail="a single book sitting loose in an author folder")
    if content_kind is ContentKind.MULTI and folder_kind is FolderKind.AUTHOR:
        return Finding(code=FindingCode.MULTI_IN_AUTHOR, severity=FindingSeverity.WARN,
                       detail=f"{len(works)} distinct works in an author folder")
    if content_kind is ContentKind.MULTI and folder_kind is FolderKind.UNDETERMINED:
        return Finding(code=FindingCode.MULTI_IN_UNDETERMINED, severity=FindingSeverity.WARN,
                       detail=f"{len(works)} distinct works; folder type undetermined")
    return None


def _duplicate_findings(
    folder_kind: FolderKind, works: list[DetectedWork], features: list[FileFeatures]
) -> list[Finding]:
    """Title-folder sub-analysis: same-work variants vs. genuinely different works."""
    if folder_kind is not FolderKind.TITLE:
        return []
    if len(works) > 1:
        return [Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR,
                        detail=f"{len(works)} different works in a title folder")]
    files = works[0].files
    exts = {p.suffix.lower() for p in files}
    if len(files) > 1 and len(exts) > 1:
        return [Finding(code=FindingCode.DUP_FORMAT, severity=FindingSeverity.INFO,
                        detail=f"same book in formats: {', '.join(sorted(exts))}")]
    narrators = {_norm(f.tags.narrator) for f in features if f.tags.narrator}
    years = {f.tags.year for f in features if f.tags.year}
    if len(files) > 1 and (len(narrators) > 1 or len(years) > 1):
        return [Finding(code=FindingCode.DUP_EDITION, severity=FindingSeverity.WARN,
                        detail="multiple editions in one folder (narrator/year differ)")]
    return []


def classify(
    folder: Path,
    root: Path,
    features: list[FileFeatures],
    *,
    template_pattern: Pattern[str],
    scheme_patterns: list[Pattern[str]],
) -> ClassificationResult:
    """Classify one folder. `features` is non-empty (a folder with no audio is
    never scanned). Pure: all signals are passed in."""
    if len(features) == 1:
        works = [_to_work(features)]
        group_signals: list[ConfidenceSignal] = []
        content_kind = ContentKind.SINGLE
    elif _fully_untagged(features):
        # No tag signal: cluster by filename structure (parts vs separate books).
        cr = cluster([f.path for f in features])
        works = cr.detected_works
        group_signals = cr.signals
        content_kind = cr.content_kind
    else:
        works, group_signals = group_works(features)
        content_kind = content_kind_for(works, group_signals)

    folder_kind, fk_signals = classify_folder_kind(
        folder, root, features, template_pattern=template_pattern, scheme_patterns=scheme_patterns
    )

    findings: list[Finding] = []
    actionable = _actionable_finding(content_kind, folder_kind, works)
    if actionable is not None:
        findings.append(actionable)
    findings.extend(_duplicate_findings(folder_kind, works, features))

    # An UNKNOWN content folder with no other finding (conflicting/absent signals)
    # would otherwise stay invisible; surface it for a human look.
    if content_kind is ContentKind.UNKNOWN and not findings:
        findings.append(Finding(
            code=FindingCode.STRUCTURE_UNCLEAR,
            severity=FindingSeverity.INFO,
            detail="multiple files but the structure could not be determined; review",
        ))

    signals = group_signals + fk_signals
    return ClassificationResult(
        content_kind=content_kind,
        folder_kind=folder_kind,
        confidence=float(sum(s.points for s in signals)),
        signals=signals,
        findings=findings,
        detected_works=works,
    )
