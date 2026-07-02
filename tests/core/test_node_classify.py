from colophon.core.node_classify import Evidence, resolve


def test_hard_evidence_settles_manual_over_matched():
    ev = [
        Evidence("author", 5.0, "matched book author 'Y'", hard=True, value="Y"),
        Evidence("series", 1.0, "one series", hard=False),
        Evidence("author", 9.0, "you classified this", hard=True, value="X"),
    ]
    got = resolve(ev, fallback_value="Folder", manual_kinds={"author"}, matched_kinds={"author"})
    assert got.kind == "author"
    assert got.confidence == 1.0
    assert got.source == "manual"
    assert got.settled is True


def test_soft_argmax_with_margin_confidence():
    ev = [
        Evidence("container", 8.0, "many child folders"),
        Evidence("author", 2.0, "a tag author matches the name"),
    ]
    got = resolve(ev, fallback_value="Folder")
    assert got.kind == "container"
    assert got.source == ""
    assert got.settled is False
    assert 0.7 < got.confidence < 0.85          # 8 / 10
    assert got.value is None                    # container carries no name


def test_soft_author_takes_value_from_evidence_else_folder():
    got = resolve([Evidence("author", 3.0, "artist consensus", value="Isaac Asimov")],
                  fallback_value="Misc SF")
    assert got.kind == "author" and got.value == "Isaac Asimov"
    got2 = resolve([Evidence("author", 3.0, "spans 4 series")], fallback_value="Sarah Graves")
    assert got2.kind == "author" and got2.value == "Sarah Graves"  # no value in evidence -> folder


def test_no_evidence_is_container():
    got = resolve([], fallback_value="X")
    assert got.kind == "container" and got.confidence == 0.0 and got.settled is False
