from colophon.core.field_resolve import FieldEvidence, resolve_field


def E(value, weight, source, hard=False):
    return FieldEvidence(value=value, weight=weight, source=source, reason=f"{source}:{value}", hard=hard)


def test_empty_ballot_resolves_to_none():
    r = resolve_field([])
    assert r.value is None and r.likelihood == 0.0


def test_zero_weight_and_blank_candidates_are_ignored():
    r = resolve_field([E(" ", 3.0, "tag"), E("", 3.0, "tag"), E("Wendy Pini", 2.5, "folder"),
                       E("junk", 0.0, "tag")])
    assert r.value == "Wendy Pini"


def test_corroboration_sums_across_sources():
    r = resolve_field([E("wrong name", 3.0, "tag"), E("Wendy Pini", 2.5, "folder"),
                       E("wendy pini", 1.5, "filename")])
    assert r.value == "Wendy Pini"
    assert 0.5 < r.likelihood <= 1.0


def test_lone_tag_beats_lone_folder():
    r = resolve_field([E("Brandon Sanderson", 3.0, "tag"), E("Some Folder", 2.5, "folder")])
    assert r.value == "Brandon Sanderson"


def test_hard_evidence_settles_over_soft():
    r = resolve_field([E("Tagged", 3.0, "tag"), E("Manual Name", 100.0, "manual", hard=True),
                       E("Folder", 2.5, "folder")])
    assert r.value == "Manual Name"
    assert r.likelihood == 1.0


def test_likelihood_is_share_of_total_weight():
    r = resolve_field([E("A", 3.0, "tag"), E("B", 1.0, "filename")])
    assert r.value == "A"
    assert r.likelihood == 0.75


def test_tie_breaks_toward_higher_base_weight():
    r = resolve_field([E("Folder Val", 2.0, "folder"),
                       E("File Val", 1.0, "filename"), E("File Val", 1.0, "sibling")])
    assert r.value == "Folder Val"


def test_two_disagreeing_hard_votes_resolve_within_hard_pool():
    # Two hard votes disagree; the higher-weight hard wins and soft votes are ignored entirely.
    r = resolve_field([E("Match Author", 10.0, "match", hard=True),
                       E("Manual Author", 100.0, "manual", hard=True),
                       E("Soft Loud", 999.0, "tag")])
    assert r.value == "Manual Author"
    assert r.likelihood == round(100.0 / 110.0, 2)   # share within the hard pool, soft excluded
