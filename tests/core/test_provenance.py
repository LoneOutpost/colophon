from colophon.core.provenance import provenance_label, provenance_tooltip


def test_local_tier_labels():
    assert provenance_label("tag") == "File tag"
    assert provenance_label("datafile") == "Datafile"
    assert provenance_label("directory") == "Folder"
    assert provenance_label("filename") == "Filename"
    assert provenance_label("graphing") == "Inferred"
    assert provenance_label("manual") == "Edited"


def test_local_tier_tooltips():
    assert provenance_tooltip("graphing") == (
        "Inferred from the author folder (a nearby tagged book named the author)."
    )
    assert provenance_tooltip("datafile") == "Read from a metadata.json datafile sidecar in the folder."


def test_non_local_values_return_none():
    assert provenance_label("audnexus") is None
    assert provenance_label(None) is None
    assert provenance_tooltip("audnexus") is None
    assert provenance_tooltip(None) is None
