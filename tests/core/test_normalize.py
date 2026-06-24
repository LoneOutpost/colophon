from colophon.core.normalize import (
    FIELD_NORMALIZERS,
    normalize_description,
    normalize_name,
    normalize_text,
)


def test_normalize_name_keeps_single_letter_capital():
    assert normalize_name("john a smith") == "John A Smith"
    assert normalize_name("a. a. milne") == "A. A. Milne"


def test_normalize_name_does_not_lowercase_small_words():
    # "van" / "de" particles and short words keep their leading capital in names
    assert normalize_name("brandon de sanderson") == "Brandon De Sanderson"


def test_normalize_text_lowercases_a_in_titles():
    # The default (title) path still treats a lone "a" as a small word.
    assert normalize_text("the lord of the rings") == "The Lord of the Rings"


def test_normalize_text_lowercases_vs():
    assert normalize_text("alien vs predator") == "Alien vs Predator"
    assert normalize_text("kramer vs. kramer") == "Kramer vs. Kramer"


def test_field_normalizers_author_is_name_aware():
    assert FIELD_NORMALIZERS["author"]("john a smith") == "John A Smith"
    assert FIELD_NORMALIZERS["narrator"]("john a smith") == "John A Smith"


def test_titlecase_lowercases_small_words_but_not_first_or_last():
    assert normalize_text("the lord of the rings") == "The Lord of the Rings"
    assert normalize_text("a tale of two cities") == "A Tale of Two Cities"


def test_titlecase_uppercases_all_caps_input():
    assert normalize_text("THE WAY OF KINGS") == "The Way of Kings"


def test_first_word_after_colon_is_capitalized():
    assert normalize_text("mistborn: the final empire") == "Mistborn: The Final Empire"


def test_underscores_and_kebab_become_spaces():
    assert normalize_text("the_way_of_kings") == "The Way of Kings"
    assert normalize_text("the-way-of-kings") == "The Way of Kings"


def test_spaced_hyphen_is_kept_as_dash_with_single_spaces():
    assert normalize_text("Mistborn  -The Final Empire") == "Mistborn - The Final Empire"
    assert normalize_text("Mistborn-  The Final Empire") == "Mistborn - The Final Empire"


def test_comma_spacing():
    assert normalize_text("Kramer ,Reading") == "Kramer, Reading"
    assert normalize_text("Kramer,Reading") == "Kramer, Reading"
    assert normalize_text("Kramer,   Reading") == "Kramer, Reading"


def test_collapses_whitespace_and_trims():
    assert normalize_text("  the   way  ") == "The Way"


def test_empty_stays_empty():
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


def test_br_becomes_newline():
    assert normalize_description("Line one<br>Line two") == "Line one\nLine two"
    assert normalize_description("Line one<br/>Line two") == "Line one\nLine two"


def test_decodes_common_entities():
    assert normalize_description("Crime &amp; Punishment") == "Crime & Punishment"
    assert normalize_description("a &lt;b&gt; c &quot;d&quot;") == 'a <b> c "d"'
    assert normalize_description("hard&nbsp;space") == "hard space"


def test_strips_tags_and_keeps_text():
    assert normalize_description("<p>Hello <i>world</i></p>") == "Hello world"


def test_collapses_excess_blank_lines_and_trims():
    assert normalize_description("a\n\n\n\nb\n\n  ") == "a\n\nb"


def test_comma_spacing_does_not_cross_newlines():
    assert normalize_description("a,b\nc , d") == "a, b\nc, d"


def test_normalize_genres_titlecases_and_dedupes():
    from colophon.core.normalize import normalize_genres
    out = normalize_genres(["fantasy", "epic fantasy", "FANTASY", "  ", "science fiction"])
    assert out == ["Fantasy", "Epic Fantasy", "Science Fiction"]


def test_normalize_genres_matches_empty_genre_policy():
    # normalize_genres and an empty GenrePolicy.canonicalize share one core, so
    # they must agree for the same input (no mapping, no whitelist).
    from colophon.core.genre_policy import GenrePolicy
    from colophon.core.normalize import normalize_genres

    raw = ["fantasy", "  ", "Epic Fantasy", "FANTASY", "science_fiction"]
    assert normalize_genres(raw) == GenrePolicy().canonicalize(raw)


def test_dedupe_normalized_applies_transform_and_keep():
    from colophon.core.normalize import dedupe_normalized

    out = dedupe_normalized(
        ["scifi", "dragons", "SCIFI"],
        transform=lambda r: {"scifi": "Science Fiction"}.get(r, r),
        keep=lambda fold: fold == "science fiction",
    )
    assert out == ["Science Fiction"]


def test_field_normalizers_has_genre_not_tag():
    from colophon.core.normalize import FIELD_NORMALIZERS, NORMALIZABLE_FIELDS
    assert "genre" in FIELD_NORMALIZERS
    assert "genre" in NORMALIZABLE_FIELDS
    assert "tag" not in FIELD_NORMALIZERS
    assert "tag" not in NORMALIZABLE_FIELDS


def test_field_normalizers_genre_normalizes_joined_value():
    from colophon.core.normalize import FIELD_NORMALIZERS
    assert FIELD_NORMALIZERS["genre"]("fantasy; epic fantasy; fantasy") == "Fantasy; Epic Fantasy"


def test_merge_preserve_existing_first_dedupe_order():
    from colophon.core.normalize import merge_preserve
    assert merge_preserve(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert merge_preserve([], ["x", "x"]) == ["x"]
    assert merge_preserve(["Keep"], []) == ["Keep"]
    assert merge_preserve(["a"], ["  ", "a", "d"]) == ["a", "d"]
