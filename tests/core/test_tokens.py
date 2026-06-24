from colophon.core.tokens import (
    BUILD_TOKENS,
    PARSE_TOKENS,
    migrate_filename_template,
    parse_field_for,
    token_by_name,
)


def test_parse_tokens_map_to_reconcile_fields():
    # every parseable token (except $Skip) carries a model field reconcile reads
    fields = {t.field for t in PARSE_TOKENS if t.field}
    assert fields == {"author", "title", "narrator", "series", "sequence", "year"}


def test_skip_is_parse_only_no_field():
    skip = token_by_name("Skip")
    assert skip is not None and skip.parses and not skip.builds and skip.field is None


def test_build_tokens_cover_renderer_keys():
    names = {t.name for t in BUILD_TOKENS}
    assert names == {
        "Author", "SortAuthor", "Title", "SortTitle", "Series", "SerName",
        "SerNum", "PadNum", "PubYear", "Narrator", "Part", "Total", "Abridged",
    }


def test_parse_field_for():
    assert parse_field_for("SerNum") == "sequence"
    assert parse_field_for("PubYear") == "year"
    assert parse_field_for("SortAuthor") is None  # build-only
    assert parse_field_for("Nope") is None


def test_migrate_filename_template():
    assert migrate_filename_template("%author% - %title%") == "$Author - $Title"
    assert migrate_filename_template("%series% #%sequence%") == "$Series #$SerNum"
    assert migrate_filename_template("%year%") == "$PubYear"
    assert migrate_filename_template("%skip% - %title%") == "$Skip - $Title"
    assert migrate_filename_template("%subtitle%") == "$Skip"  # dead no-op -> discard segment


def test_migrate_is_idempotent_and_leaves_unknown():
    assert migrate_filename_template("$Author - $Title") == "$Author - $Title"
    assert migrate_filename_template("%bogus%") == "%bogus%"  # unknown left verbatim


def test_sername_is_hidden_alias():
    from colophon.core.tokens import token_by_name
    t = token_by_name("SerName")
    assert t is not None and t.builds and t.hidden  # still renders, but not advertised


def test_migrate_directory_scheme():
    from colophon.core.tokens import migrate_directory_scheme
    assert migrate_directory_scheme("Author/Series/Title") == "$Author/$Series/$Title"
    assert migrate_directory_scheme("Author/Foo/Title") == "$Author/$Skip/$Title"  # unknown -> skip
    assert migrate_directory_scheme("") == ""
    assert migrate_directory_scheme("$Author/$Series") == "$Author/$Series"  # idempotent
