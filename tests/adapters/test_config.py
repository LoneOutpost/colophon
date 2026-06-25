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
    assert cfg.filename_template == "$Author - $Title"
    assert cfg.library_root is None


def test_new_config_fields_round_trip(tmp_path):
    from pathlib import Path

    path = tmp_path / "c.toml"
    cfg = Config(
        db_path=Path("/data/colophon.db"),
        filename_template="$Title",
        library_root=Path("/library"),
    )
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_load_migrates_legacy_percent_template(tmp_path):
    path = tmp_path / "c.toml"
    save_config(Config(filename_template="%author% - %title%",
                       recent_filename_templates=["%series% #%sequence%"]), path)
    cfg = load_config(path)
    assert cfg.filename_template == "$Author - $Title"
    assert cfg.recent_filename_templates == ["$Series #$SerNum"]


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


def test_port_and_root_path_defaults(tmp_path):
    cfg = load_config(tmp_path / "c.toml")
    assert cfg.port == 8080
    assert cfg.root_path == ""


def test_port_and_root_path_round_trip(tmp_path):
    path = tmp_path / "c.toml"
    cfg = Config(port=9000, root_path="/colophon")
    save_config(cfg, path)
    restored = load_config(path)
    assert restored.port == 9000
    assert restored.root_path == "/colophon"


def test_integration_fields_round_trip(tmp_path):
    path = tmp_path / "c.toml"
    cfg = Config(
        audiobookshelf_url="http://abs.local:13378",
        audiobookshelf_token="abs-tok",
        audiobookshelf_library_id="lib_1",
    )
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_real_debrid_fields_round_trip(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config

    cfg = Config(real_debrid_token="rd_tok", real_debrid_download_dir=tmp_path / "dl")
    path = tmp_path / "c.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.real_debrid_token == "rd_tok"
    assert loaded.real_debrid_download_dir == tmp_path / "dl"


def test_real_debrid_defaults_are_none():
    from colophon.adapters.config import Config

    cfg = Config()
    assert cfg.real_debrid_token is None
    assert cfg.real_debrid_download_dir is None


def test_genre_policy_fields_default_empty():
    c = Config()
    assert c.genre_mapping == {}
    assert c.accepted_genres == []
    assert c.genre_whitelist_enabled is False


def test_genre_policy_fields_round_trip(tmp_path):
    cfg_path = tmp_path / "config.toml"
    c = Config(
        genre_mapping={"scifi": "Science Fiction"},
        accepted_genres=["Science Fiction", "Fantasy"],
        genre_whitelist_enabled=True,
    )
    save_config(c, cfg_path)
    loaded = load_config(cfg_path)
    assert loaded.genre_mapping == {"scifi": "Science Fiction"}
    assert loaded.accepted_genres == ["Science Fiction", "Fantasy"]
    assert loaded.genre_whitelist_enabled is True


def test_storage_secret_defaults_none():
    from colophon.adapters.config import Config
    assert Config().storage_secret is None


def test_source_prefs_round_trip(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config

    cfg = Config(source_order=["audnexus", "hardcover"], disabled_sources=["googlebooks"])
    path = tmp_path / "c.toml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.source_order == ["audnexus", "hardcover"]
    assert loaded.disabled_sources == ["googlebooks"]


def test_source_prefs_default_empty():
    from colophon.adapters.config import Config

    cfg = Config()
    assert cfg.source_order == []
    assert cfg.disabled_sources == []


def test_organize_pattern_defaults():
    from colophon.adapters.config import Config

    cfg = Config()
    assert cfg.organize_folder_pattern == "$Author/$Title"
    assert cfg.organize_file_pattern == "$Title"


def test_organize_patterns_round_trip(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config

    cfg = Config(
        organize_folder_pattern="$SortAuthor/$Series #$PadNum - $Title",
        organize_file_pattern="$Title ($PubYear)",
    )
    path = tmp_path / "c.toml"
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_legacy_lazylibrarian_keys_are_ignored(tmp_path):
    from colophon.adapters.config import load_config

    path = tmp_path / "c.toml"
    path.write_text(
        'lazylibrarian_url = "http://old"\n'
        'lazylibrarian_api_key = "k"\n'
        'lazylibrarian_config_ini = "/x/config.ini"\n'
    )
    cfg = load_config(path)  # must not raise; the dead LazyLibrarian keys are dropped
    assert not hasattr(cfg, "lazylibrarian_url")
    assert not hasattr(cfg, "lazylibrarian_api_key")
    assert not hasattr(cfg, "lazylibrarian_config_ini")


def test_downloads_scan_prompt_flag_round_trips(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config

    cfg = Config(downloads_scan_prompt_seen=True)
    path = tmp_path / "c.toml"
    save_config(cfg, path)
    assert load_config(path).downloads_scan_prompt_seen is True


def test_downloads_scan_prompt_flag_defaults_false():
    from colophon.adapters.config import Config

    assert Config().downloads_scan_prompt_seen is False


def test_settings_save_preserves_non_form_fields():
    """Guard for #111: the Settings page builds the saved config with
    cfg.model_copy(update={form fields}), so fields the form does not edit must
    survive a save instead of resetting to defaults."""
    from colophon.adapters.config import Config

    cfg = Config(
        storage_secret="secret-xyz",
        recent_filename_templates=["$Series #$SerNum - $Title"],
        downloads_scan_prompt_seen=True,
        hardcover_api_token="hc-token",
        filename_template="$Author - $Title",
    )
    # the form edits only its own fields (mirrors settings.do_save)
    saved = cfg.model_copy(update={"filename_template": "$Title", "port": 9000})

    assert saved.filename_template == "$Title"  # form field changed
    assert saved.port == 9000
    # non-form fields preserved, not reset to defaults
    assert saved.storage_secret == "secret-xyz"
    assert saved.recent_filename_templates == ["$Series #$SerNum - $Title"]
    assert saved.downloads_scan_prompt_seen is True
    assert saved.hardcover_api_token == "hc-token"


def test_load_migrates_legacy_directory_scheme(tmp_path):
    from colophon.adapters.config import Config, load_config, save_config
    path = tmp_path / "c.toml"
    save_config(Config(directory_scheme="Author/Series/Title"), path)
    assert load_config(path).directory_scheme == "$Author/$Series/$Title"


def test_pattern_history_defaults_empty():
    from colophon.adapters.config import Config
    cfg = Config()
    assert cfg.recent_filename_templates == []
    assert cfg.recent_directory_schemes == []
    assert cfg.recent_organize_patterns == []


def test_saved_filename_patterns_migrates_into_recent(tmp_path):
    import tomli_w

    from colophon.adapters.config import load_config
    path = tmp_path / "c.toml"
    with path.open("wb") as f:
        tomli_w.dump({"saved_filename_patterns": ["%author% - %title%", "%title%"]}, f)
    cfg = load_config(path)
    assert cfg.recent_filename_templates == ["$Author - $Title", "$Title"]


def test_organize_pattern_round_trips(tmp_path):
    from colophon.adapters.config import Config, OrganizePattern, load_config, save_config
    path = tmp_path / "c.toml"
    save_config(Config(recent_organize_patterns=[OrganizePattern(folder="$Author", file="$Title")]), path)
    assert load_config(path).recent_organize_patterns == [OrganizePattern(folder="$Author", file="$Title")]
