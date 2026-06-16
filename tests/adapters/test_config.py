from pathlib import Path

from colophon.adapters.config import Config, load_config, save_config


def test_defaults_when_file_absent(tmp_path: Path):
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.scan_paths == []
    assert cfg.review_threshold == 75.0
    assert cfg.transcode_bitrate == "64k"
    assert cfg.worker_pool_size is None


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = Config(
        scan_paths=[Path("/ingest/audiobooks")],
        lazylibrarian_config_ini=Path("/ll/config.ini"),
        review_threshold=80.0,
        transcode_bitrate="128k",
        worker_pool_size=4,
    )
    save_config(cfg, path)
    restored = load_config(path)
    assert restored == cfg
    assert restored.scan_paths == [Path("/ingest/audiobooks")]


def test_save_creates_parent_directories(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "config.toml"
    save_config(Config(), path)
    assert path.exists()


def test_new_config_fields_have_defaults(tmp_path):
    cfg = load_config(tmp_path / "c.toml")
    assert cfg.db_path is None
    assert cfg.filename_template == "%author% - %title%"
    assert cfg.library_root is None


def test_new_config_fields_round_trip(tmp_path):
    from pathlib import Path

    path = tmp_path / "c.toml"
    cfg = Config(
        db_path=Path("/data/colophon.db"),
        filename_template="%title%",
        library_root=Path("/library"),
    )
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_ensure_config_file_creates_when_absent(tmp_path):
    from colophon.adapters.config import ensure_config_file

    path = tmp_path / "config.toml"
    created = ensure_config_file(path)
    assert created is True
    assert path.exists()


def test_ensure_config_file_noop_when_present(tmp_path):
    from colophon.adapters.config import ensure_config_file

    path = tmp_path / "config.toml"
    path.write_text("scan_paths = []\n")
    created = ensure_config_file(path)
    assert created is False
    assert path.read_text() == "scan_paths = []\n"  # untouched


def test_generated_config_loads_to_defaults(tmp_path):
    from colophon.adapters.config import ensure_config_file

    path = tmp_path / "config.toml"
    ensure_config_file(path)
    # the generated file's active keys must round-trip to the same defaults
    assert load_config(path) == Config()


def test_hardcover_token_defaults_none(tmp_path):
    assert load_config(tmp_path / "c.toml").hardcover_api_token is None


def test_hardcover_token_round_trips(tmp_path):
    path = tmp_path / "c.toml"
    cfg = Config(hardcover_api_token="hc-token")
    save_config(cfg, path)
    assert load_config(path).hardcover_api_token == "hc-token"


def test_integration_fields_default_none(tmp_path):
    cfg = load_config(tmp_path / "c.toml")
    assert cfg.audiobookshelf_url is None
    assert cfg.audiobookshelf_token is None
    assert cfg.audiobookshelf_library_id is None
    assert cfg.lazylibrarian_url is None
    assert cfg.lazylibrarian_api_key is None


def test_integration_fields_round_trip(tmp_path):
    path = tmp_path / "c.toml"
    cfg = Config(
        audiobookshelf_url="http://abs.local:13378",
        audiobookshelf_token="abs-tok",
        audiobookshelf_library_id="lib_1",
        lazylibrarian_url="http://ll.local:5299",
        lazylibrarian_api_key="ll-key",
    )
    save_config(cfg, path)
    assert load_config(path) == cfg
