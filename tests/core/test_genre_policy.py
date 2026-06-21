from colophon.core.genre_policy import GenrePolicy


def test_canonicalize_maps_synonyms_case_insensitive():
    p = GenrePolicy(mapping={"sci-fi": "Science Fiction", "scifi": "Science Fiction"})
    assert p.canonicalize(["SciFi", "sci-fi", "Fantasy"]) == ["Science Fiction", "Fantasy"]


def test_canonicalize_filters_only_when_enabled():
    p = GenrePolicy(accepted=["Science Fiction", "Fantasy"], whitelist_enabled=True)
    assert p.canonicalize(["Science Fiction", "Dragons", "Fantasy"]) == ["Science Fiction", "Fantasy"]


def test_canonicalize_no_filter_when_disabled():
    p = GenrePolicy(accepted=["Science Fiction"], whitelist_enabled=False)
    assert p.canonicalize(["Science Fiction", "Dragons"]) == ["Science Fiction", "Dragons"]


def test_canonicalize_no_filter_when_accepted_empty():
    p = GenrePolicy(accepted=[], whitelist_enabled=True)
    assert p.canonicalize(["Anything"]) == ["Anything"]


def test_canonicalize_empty_policy_equals_normalize():
    p = GenrePolicy()
    assert p.canonicalize(["sci_fi", "  ", "Fantasy", "fantasy"]) == ["Sci Fi", "Fantasy"]


def test_canonicalize_map_then_filter_and_dedupe():
    p = GenrePolicy(
        mapping={"scifi": "Science Fiction", "sci-fi": "Science Fiction"},
        accepted=["Science Fiction"],
        whitelist_enabled=True,
    )
    assert p.canonicalize(["scifi", "sci-fi", "Dragons"]) == ["Science Fiction"]


def test_canonicalize_accepted_compares_case_insensitively():
    p = GenrePolicy(accepted=["science fiction"], whitelist_enabled=True)
    assert p.canonicalize(["Science Fiction"]) == ["Science Fiction"]
