from colophon.adapters.config import Config
from colophon.app_context import AppContext


def test_create_wires_db_repos_and_sources(tmp_path):
    cfg = Config(db_path=tmp_path / "colophon.db", library_root=tmp_path / "lib")
    ctx = AppContext.create(cfg)
    # db migrated -> repos usable
    assert ctx.books.list_all() == []
    assert ctx.history.latest_batch_id() is None
    # default sources present and named
    names = {s.name for s in ctx.sources}
    assert names == {"audnexus", "openlibrary", "googlebooks", "internetarchive"}
    assert ctx.config.library_root == tmp_path / "lib"
    ctx.close()


def test_create_uses_default_db_when_unset(tmp_path, monkeypatch):
    # point the default location into tmp so we don't touch real user dirs
    monkeypatch.setattr("colophon.app_context.default_db_path", lambda: tmp_path / "d.db")
    ctx = AppContext.create(Config())
    assert (tmp_path / "d.db").exists()
    ctx.close()


def test_clients_none_when_unconfigured(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"))
    assert ctx.abs_client is None
    assert not hasattr(ctx, "ll_client")
    ctx.close()


def test_clients_built_when_configured(tmp_path):
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite",
        audiobookshelf_url="http://abs.local", audiobookshelf_token="t",
    ))
    from colophon.adapters.audiobookshelf import AbsClient
    assert isinstance(ctx.abs_client, AbsClient)
    assert not hasattr(ctx, "ll_client")
    ctx.close()


def test_patterns_built_from_config_fields(tmp_path):
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite",
        organize_folder_pattern="$SortAuthor/$Title",
        organize_file_pattern="$Title",
    ))
    assert ctx.patterns.folder == "$SortAuthor/$Title"
    assert ctx.patterns.single_file == "$Title"
    ctx.close()


def test_abs_agg_sources_discovered_when_url_set(tmp_path, monkeypatch):
    import colophon.app_context as appctx
    class _FakeProvider:
        name = "absagg-provider"

    monkeypatch.setattr(
        appctx, "discover_providers",
        lambda url: [_FakeProvider()] if url else [],
    )
    base = AppContext.create(Config(db_path=tmp_path / "a.db"))
    base_count = len(base.sources)
    base.close()

    with_agg = AppContext.create(Config(db_path=tmp_path / "b.db", abs_agg_url="http://abs-agg"))
    assert len(with_agg.sources) == base_count + 1
    with_agg.close()


def test_config_path_defaults_to_standard_location(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"))
    from colophon.adapters.config import default_config_path
    assert ctx.config_path == default_config_path()
    ctx.close()


def test_config_path_override(tmp_path):
    cfg_path = tmp_path / "custom.toml"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite"), config_path=cfg_path)
    assert ctx.config_path == cfg_path
    ctx.close()


def test_arrange_sources_orders_filters_and_appends_new():
    from colophon.app_context import arrange_sources

    class S:
        def __init__(self, name): self.name = name
        def __repr__(self): return f"S({self.name})"

    a, b, c, d = S("audnexus"), S("googlebooks"), S("openlibrary"), S("hardcover")
    arranged = arrange_sources(
        [a, b, c, d],
        order=["openlibrary", "audnexus"],   # known order; b & d unlisted
        disabled=["googlebooks"],            # b filtered out
    )
    assert [s.name for s in arranged] == ["openlibrary", "audnexus", "hardcover"]


def test_arrange_sources_ignores_stale_order_and_disabled():
    from colophon.app_context import arrange_sources

    class S:
        def __init__(self, name): self.name = name

    a = S("audnexus")
    arranged = arrange_sources([a], order=["ghost", "audnexus"], disabled=["alsogone"])
    assert [s.name for s in arranged] == ["audnexus"]
