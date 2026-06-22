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
    assert ctx.ll_client is None
    ctx.close()


def test_clients_built_when_configured(tmp_path):
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite",
        audiobookshelf_url="http://abs.local", audiobookshelf_token="t",
        lazylibrarian_url="http://ll.local", lazylibrarian_api_key="k",
    ))
    from colophon.adapters.audiobookshelf import AbsClient
    from colophon.adapters.lazylibrarian_api import LazyLibrarianClient
    assert isinstance(ctx.abs_client, AbsClient)
    assert isinstance(ctx.ll_client, LazyLibrarianClient)
    ctx.close()


def test_hardcover_source_added_only_when_token_present(tmp_path):
    base = AppContext.create(Config(db_path=tmp_path / "a.db"))
    assert "hardcover" not in {s.name for s in base.sources}
    base.close()

    with_hc = AppContext.create(Config(db_path=tmp_path / "b.db", hardcover_api_token="hc"))
    assert "hardcover" in {s.name for s in with_hc.sources}
    with_hc.close()


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
