from colophon.core.sequence_marker import (
    SeqKind,
    find_parts,
    parse_part,
    strip_parts,
)


def test_parse_part_recognizes_spaced_and_glued_forms():
    for token, index, total, sub in [
        ("01 of 15", 1, 15, ""), ("1of9", 1, 9, ""), ("1 of 9", 1, 9, ""),
        ("Disc 05", 5, None, ""), ("CD02", 2, None, ""), ("Disc05", 5, None, ""),
        ("Part 3", 3, None, ""), ("Ch01", 1, None, ""), ("Track 7", 7, None, ""),
        ("Disc 08 of 11", 8, 11, ""), ("13b", 13, None, "b"), ("pt2", 2, None, ""),
    ]:
        m = parse_part(token)
        assert m is not None, token
        assert (m.kind, m.index, m.total, m.subpart) == (SeqKind.PART, index, total, sub), token


def test_parse_part_rejects_bare_numbers_and_book_axis():
    # a bare number is ambiguous (track? series ordinal?) -> not a PART on its own
    assert parse_part("05") is None
    assert parse_part("1984") is None
    # BOOK-axis ordinals are a separate box (parse_series_ref), never a PART
    for t in ("Bk01", "#16", "Book 7", "Casca 05"):
        assert parse_part(t) is None, t


def test_render_is_a_single_unmistakable_token():
    assert parse_part("01 of 15").render() == "‹p1/15›"
    assert parse_part("13b").render() == "‹p13b›"
    assert parse_part("Disc 05").render() == "‹p5›"


def test_span_covers_its_whole_run():
    m = parse_part("01-06 of 20")
    assert m is not None and m.index == 1 and m.end == 6 and m.total == 20
    assert m.covers() == {1, 2, 3, 4, 5, 6}
    assert parse_part("Disc 1-3").covers() == {1, 2, 3}
    # a bare NN-NN range (no marker word / of-total) stays ambiguous and is not a part
    assert parse_part("01-12") is None


def test_find_parts_locates_markers_glued_to_title_text():
    assert [mk.render() for _, mk in find_parts("Hot Mahogany 7of7")] == ["‹p7/7›"]
    assert [mk.render() for _, mk in find_parts("The Skies of Pern Disc 08 of 11")] == ["‹p8/11›"]
    # 'Part of Valor' is not a part (no digit after 'Part'); only '01 of 20' is
    assert [mk.render() for _, mk in find_parts("The Better Part of Valor 01 of 20")] == ["‹p1/20›"]
    # an intra-word hyphen number and a bare number are never scanned
    assert find_parts("Catch-22") == []
    assert find_parts("Slaughterhouse 5") == []


def test_strip_parts_leaves_the_identity_material():
    assert strip_parts("Hot Mahogany 7of7") == "Hot Mahogany"
    assert strip_parts("Caves of Steel - 3 of 3") == "Caves of Steel"
    assert strip_parts("Dark Jenny Part01") == "Dark Jenny"
    assert strip_parts("Catch-22") == "Catch-22"
