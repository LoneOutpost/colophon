"""Pure structural classifier for a scanned folder.

Fed the grouped files plus their embedded tags, it determines how many distinct
works the folder holds (content axis) and what the folder represents (folder
axis), and emits findings. No I/O of its own: callers read tags and hand them in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
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
    Provenance,
)
from colophon.core.normalize import normalize_key, normalize_text


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


def _text_key(s: str | None) -> str:
    """Generic casefold/whitespace key for comparing free text (titles, albums, stems). NOT an
    entity key: for person-name equality (author/narrator) use `normalize_key`, which also folds
    diacritics and 'Last, First' order so it agrees with node_classify."""
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
    name = _text_key(folder.name)              # for title/album/stem (free text)
    name_key = normalize_key(folder.name)      # for author (canonical person-name key)

    artists = {normalize_key(f.tags.artist) for f in features if f.tags.artist}
    albums = {_text_key(f.tags.album) for f in features if f.tags.album}
    titles = {_text_key(f.tags.title) for f in features if f.tags.title}
    if name_key and name_key in artists:
        return FolderKind.AUTHOR, [_signal("foldername_is_artist", 3, "folder name matches embedded artist")]
    if name and (name in albums or name in titles):
        return FolderKind.TITLE, [_signal("foldername_is_album", 3, "folder name matches embedded album/title")]

    inferred = infer_from_path(folder, root, scheme_patterns)
    fn_fields = parse_filename(template_pattern, features[0].path.name) or {}
    if name_key and (normalize_key(inferred.get("author") or "") == name_key
                     or normalize_key(fn_fields.get("author") or "") == name_key):
        return FolderKind.AUTHOR, [_signal("foldername_is_parsed_author", 2, "folder name matches parsed author")]
    if name and (_text_key(inferred.get("title")) == name or _text_key(fn_fields.get("title")) == name):
        return FolderKind.TITLE, [_signal("foldername_is_parsed_title", 2, "folder name matches parsed title")]

    if name and any(_text_key(f.path.stem) == name for f in features):
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
        return f"album:{_text_key(t.album)}"
    return None


def _fully_untagged(features: list[FileFeatures]) -> bool:
    """True when no file carries a tag-based work key (asin/isbn/album)."""
    return all(_work_key(f) is None for f in features)


def _cluster_works(files: list[FileFeatures]) -> tuple[list[DetectedWork], list[ConfidenceSignal]]:
    """Delegate a set of files to the filename clusterer. `cluster` is the one place that decides
    "same title, differ only by number = one book's parts" vs "distinct title text = separate
    books"; grouping reuses it here rather than reimplementing that reasoning less capably."""
    cr = cluster([f.path for f in files])
    return cr.detected_works, cr.signals


def _first(values) -> str | None:
    for v in values:
        if v:
            return v
    return None


# Generic placeholder tag values that are not a real title (a rip left the default in): they must
# never win over the filename. "Track 3", "Disc 1", "CD 2", "Chapter 5", "Volume 1", "Unknown
# Album …", "Untitled".
_PLACEHOLDER = re.compile(
    r"^(?:(?:track|disc|cd|chapter|volume|vol)\s*\d+|unknown(?:\s.*)?|untitled)$", re.IGNORECASE
)


def _is_placeholder(value: str | None) -> bool:
    return bool(value) and bool(_PLACEHOLDER.match(value.strip()))


_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def _dearticled(value: str) -> str:
    """`_text_key` with a leading article dropped, so "The Bacta War" and "Bacta War" compare equal."""
    return _text_key(_ARTICLE.sub("", value.strip()))


def _tag_is_typo_of(tag: str, filename_title: str) -> bool:
    """True when the Title tag looks like a rip typo of the filename title: near-identical text that
    differs by *more than* a leading article. Article-only differences ("The Bacta War" vs "Bacta
    War") are real improvements and favor the tag; a stray character or spacing slip ("Issard's" vs
    "Isard's", "Blood lines" vs "Bloodlines") is a typo and defers to the filename. This is the
    weighted cross-check between the two sources — a first cut, tunable by the ratio threshold."""
    if not filename_title or _dearticled(tag) == _dearticled(filename_title):
        return False
    return SequenceMatcher(None, _text_key(tag), _text_key(filename_title)).ratio() >= 0.85


def _pick_single_title(
    title: str | None, album: str | None, fw: DetectedWork
) -> tuple[str, str]:
    """The book title (and its provenance) for one file, favoring the tag but cross-checking the
    filename. `fw` is the filename-parsed work: `fw.label` is the title portion, `fw.series` the
    `(Series N)` parenthetical.

    Tags are unreliably filed — the Title tag usually holds the book title, but some files put the
    *series* there and the title in the Album (e.g. "Allies (Fate Of The Jedi 5)"), some carry only a
    junk placeholder ("Track 1"), and some carry a typo of the real title. So a tag that equals the
    filename's series or is a placeholder is rejected; a tag that is a near-duplicate typo of the
    filename title defers to the filename; otherwise the tag wins. A structured filename (one that
    named a series) is trusted for the title over a bare Album (usually the series or franchise)."""
    def unusable(v: str) -> bool:              # a placeholder, or actually the filename's series
        return _is_placeholder(v) or _text_key(v) == _text_key(fw.series)

    if title and not unusable(title):
        if _tag_is_typo_of(title, fw.label):   # rip typo of the filename title -> trust the filename
            return fw.label, Provenance.FILENAME.value
        return title, Provenance.TAG.value     # favor the tag (article restored, de-prefixed, cleaner)
    if fw.series and fw.label:                 # structured filename: its parsed title is reliable
        return fw.label, Provenance.FILENAME.value
    if album and not unusable(album):          # unstructured filename: the Album is the title
        return album, Provenance.TAG.value
    return (fw.label or normalize_text(fw.files[0].stem)), Provenance.FILENAME.value


def _overlay_tags(sub_works: list[DetectedWork], group: list[FileFeatures]) -> list[DetectedWork]:
    """Re-title each single-file work the clusterer produced, favoring that file's Title tag over the
    filename it parsed. The clusterer only reads filenames; this lets a shared-series shelf (X-Wing,
    Legacy of the Force) take each book's clean Title tag while keeping the filename-parsed series."""
    from colophon.core.normalize import proper_case_if_shouting

    feat_by_path = {f.path: f for f in group}
    out: list[DetectedWork] = []
    for w in sub_works:
        f = feat_by_path.get(w.files[0]) if len(w.files) == 1 else None
        if f is None:
            out.append(w)
            continue
        label, prov = _pick_single_title(f.tags.title, f.tags.album, w)
        out.append(w.model_copy(update={
            "label": proper_case_if_shouting(label),
            "label_prov": prov,
            "author": f.tags.artist or w.author,
        }))
    return out


def _to_work(group: list[FileFeatures]) -> DetectedWork:
    from colophon.core.normalize import proper_case_if_shouting

    title = _first(f.tags.title for f in group)
    album = _first(f.tags.album for f in group)
    author = _first(f.tags.artist for f in group)
    if len(group) > 1:
        # A multi-file group is one book's chapters: the Album is the book's title and each file's
        # Title is a chapter, so the Album labels the work (ignoring a junk placeholder album).
        tag_label = next((v for v in (album, title) if v and not _is_placeholder(v)), None)
        label = tag_label or normalize_text(group[0].path.stem)
        prov = Provenance.TAG.value if tag_label else Provenance.FILENAME.value
        return DetectedWork(label=proper_case_if_shouting(label), label_prov=prov,
                            author=author, files=[f.path for f in group])
    # A single file is one whole book. Pick its title with the filename as arbiter; the series and
    # its sequence number come from the filename (the clusterer already parses "Title (Series N)").
    fw = cluster([group[0].path]).detected_works[0]
    label, label_prov = _pick_single_title(title, album, fw)
    return DetectedWork(
        label=proper_case_if_shouting(label),
        label_prov=label_prov,
        author=author,
        series=fw.series,
        sequence=fw.sequence,
        files=[group[0].path],
    )


def group_works(features: list[FileFeatures]) -> tuple[list[DetectedWork], list[ConfidenceSignal]]:
    """Group files into distinct works. `asin`/`isbn` are per-book identifiers and always group
    as one work. `album` is ambiguous — it may be a book title (files are that book's parts) or a
    *series* name (files are distinct books) — so a multi-file album group is re-checked by the
    clusterer, which splits it when the filenames carry distinct titles. Unkeyed files are handed
    to the same clusterer."""
    signals: list[ConfidenceSignal] = []
    keyed: dict[str, list[FileFeatures]] = {}
    unkeyed: list[FileFeatures] = []
    for f in features:
        key = _work_key(f)
        if key is None:
            unkeyed.append(f)
        else:
            keyed.setdefault(key, []).append(f)

    works: list[DetectedWork] = []
    for key, group in keyed.items():
        if key.startswith("album:") and len(group) > 1:
            sub, sub_signals = _cluster_works(group)
            if len(sub) > 1:  # the album was a series name over several distinct-title books
                works.extend(_overlay_tags(sub, group))
                signals.extend(sub_signals)
                continue
        works.append(_to_work(group))
    if keyed:
        signals.append(_signal("tag_work_keys", 2 * len(keyed), f"{len(keyed)} work key(s) from tags"))

    if len(unkeyed) > 1:
        sub, sub_signals = _cluster_works(unkeyed)
        works.extend(_overlay_tags(sub, unkeyed))
        signals.extend(sub_signals)
    elif unkeyed:
        works.append(_to_work(unkeyed))

    return works, signals


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


_EMPTY_AUDIO_MIN_SIZE = 64 * 1024  # ignore sub-64KB stray files; a real audiobook is megabytes


def empty_audio_finding(sized_durations: list[tuple[int, float]]) -> Finding | None:
    """The EMPTY_AUDIO finding for a book, from its files' (size, duration) pairs — or None. A file
    with real size but zero readable duration is corrupt or an incomplete download (neither mutagen
    nor ffprobe found audio in it); flag it so it doesn't masquerade as a normal 0:00 entry. A size
    floor ignores stray sub-64KB artifacts. Shared by scan (CATEGORIZE) and the re-probe pass."""
    bad = sum(1 for size, dur in sized_durations if dur <= 0 and size > _EMPTY_AUDIO_MIN_SIZE)
    if not bad:
        return None
    detail = (f"{bad} files have no readable audio (corrupt or an incomplete download)" if bad > 1
              else "the audio file has no readable content (corrupt or an incomplete download)")
    return Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail=detail)


def _empty_audio_finding(features: list[FileFeatures]) -> Finding | None:
    return empty_audio_finding([(_file_size(f.path), f.duration_seconds) for f in features])


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


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
    narrators = {_text_key(f.tags.narrator) for f in features if f.tags.narrator}
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
    force_single: bool = False,
) -> ClassificationResult:
    """Classify one folder. `features` is non-empty (a folder with no audio is
    never scanned). Pure: all signals are passed in. `force_single` (a user's Combine)
    overrides grouping so every file becomes one book's chapters, whatever the filenames."""
    if force_single:
        works = [_to_work(features)]
        group_signals = []
        content_kind = ContentKind.SINGLE
    elif len(features) == 1:
        if _fully_untagged(features):
            cr = cluster([features[0].path])     # let cluster parse series/sequence
            works = cr.detected_works
            group_signals = cr.signals
        else:
            works = [_to_work(features)]
            group_signals = []
        content_kind = ContentKind.SINGLE
    elif _fully_untagged(features):
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
    empty = _empty_audio_finding(features)
    if empty is not None:
        findings.append(empty)
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
