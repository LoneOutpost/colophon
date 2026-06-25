from pathlib import Path

from colophon.core.models import (
    BookUnit,
    ContentKind,
    DetectedWork,
    Finding,
    FindingCode,
    FindingSeverity,
    FolderKind,
)


def test_new_book_has_classification_defaults():
    b = BookUnit.new(source_folder=Path("/x"))
    assert b.content_kind is ContentKind.UNKNOWN
    assert b.folder_kind is FolderKind.UNDETERMINED
    assert b.classification_confidence == 0.0
    assert b.classification_signals == []
    assert b.findings == []
    assert b.detected_works == []
    assert b.acknowledged_findings == []


def test_finding_and_detected_work_roundtrip():
    f = Finding(code=FindingCode.MULTI_IN_AUTHOR, severity=FindingSeverity.WARN, detail="2 works")
    w = DetectedWork(label="Legion", author="Brandon Sanderson", files=[Path("/a/Legion.mp3")])
    b = BookUnit.new(source_folder=Path("/x"))
    b.findings = [f]
    b.detected_works = [w]
    loaded = BookUnit.model_validate(b.model_dump())
    assert loaded.findings[0].code is FindingCode.MULTI_IN_AUTHOR
    assert loaded.detected_works[0].label == "Legion"


def test_old_row_without_new_fields_loads_with_defaults():
    # A stored blob predating this feature has none of the new keys.
    b = BookUnit.new(source_folder=Path("/x"))
    blob = b.model_dump()
    for key in ("content_kind", "folder_kind", "classification_confidence",
                "classification_signals", "findings", "detected_works",
                "acknowledged_findings"):
        blob.pop(key, None)
    loaded = BookUnit.model_validate(blob)
    assert loaded.content_kind is ContentKind.UNKNOWN
    assert loaded.findings == []
