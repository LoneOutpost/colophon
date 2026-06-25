"""Application configuration, persisted as TOML under the XDG config dir."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from platformdirs import user_config_path
from pydantic import BaseModel

from colophon.core.tokens import migrate_directory_scheme, migrate_filename_template

PATTERN_HISTORY_CAP = 10  # max entries kept per pattern-history list


class OrganizePattern(BaseModel):
    folder: str
    file: str


class Config(BaseModel):
    scan_paths: list[Path] = []
    review_threshold: float = 75.0
    transcode_bitrate: str = "64k"
    worker_pool_size: int | None = None  # None => default to cpu_count - 1
    port: int = 8080
    root_path: str = ""  # URL base path behind a reverse proxy; "" serves at "/"
    db_path: Path | None = None             # None => default user-data location
    filename_template: str = "$Author - $Title"
    recent_filename_templates: list[str] = []      # newest-first, capped history (parse + scan)
    recent_directory_schemes: list[str] = []        # newest-first, capped
    recent_organize_patterns: list[OrganizePattern] = []  # newest-first, capped (folder+file pairs)
    directory_scheme: str = ""  # e.g. "Author/Series/Title"; "" disables directory inference
    organize_folder_pattern: str = "$Author/$Title"  # LazyLibrarian-style $Token folder grammar
    organize_file_pattern: str = "$Title"  # the M4B file name pattern (no extension)
    library_root: Path | None = None        # destination root for organized M4Bs
    audiobookshelf_url: str | None = None
    audiobookshelf_token: str | None = None
    audiobookshelf_library_id: str | None = None
    hardcover_api_token: str | None = None
    abs_agg_url: str | None = None  # base URL of a self-hosted abs-agg, e.g. http://host:3000
    storage_secret: str | None = None  # generated on first run; signs NiceGUI tab storage
    real_debrid_token: str | None = None
    real_debrid_download_dir: Path | None = None  # None => <data dir>/downloads
    downloads_scan_prompt_seen: bool = False  # have we offered to add the downloads dir to scan paths
    genre_mapping: dict[str, str] = {}
    accepted_genres: list[str] = []
    genre_whitelist_enabled: bool = False
    normalize_on_match: list[str] = []
    source_order: list[str] = []  # provider names, highest authority first; [] = default order
    disabled_sources: list[str] = []  # provider names excluded from matching


def default_config_path() -> Path:
    return user_config_path("colophon") / "config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    # Legacy saved_filename_patterns -> recent_filename_templates (migrated to $Token), unless
    # the config is already on the new schema. Read from raw TOML since the field is removed.
    legacy = data.pop("saved_filename_patterns", None)
    if legacy and "recent_filename_templates" not in data:
        seen: list[str] = []
        for p in (migrate_filename_template(x) for x in legacy):
            if p and p not in seen:
                seen.append(p)
        data["recent_filename_templates"] = seen[:PATTERN_HISTORY_CAP]
    cfg = Config.model_validate(data)
    # Migrate legacy %placeholder% parse templates to the unified $Token grammar.
    cfg.filename_template = migrate_filename_template(cfg.filename_template)
    cfg.recent_filename_templates = [migrate_filename_template(p) for p in cfg.recent_filename_templates]
    # Migrate a legacy bare directory scheme ('Author/Series/Title') to $Token form.
    cfg.directory_scheme = migrate_directory_scheme(cfg.directory_scheme)
    return cfg


def save_config(config: Config, path: Path | None = None) -> None:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json", exclude_none=True)
    with path.open("wb") as f:
        tomli_w.dump(payload, f)


DEFAULT_CONFIG_TEMPLATE = """\
# Colophon configuration.
# Auto-generated with defaults on first run. Edit values and restart, or use
# the Settings page in the web interface. Lines starting with '#' are comments.
# See the Configuration reference in README.md for full descriptions.

# Folders to scan for audiobooks, one entry per line in the list. Each folder
# that directly contains audio files is treated as one book.
# Required before scanning will find anything.
scan_paths = []

# Where organized M4B files are written. Required before encoding/organizing.
# library_root = "/path/to/audiobooks/library"

# Template used to parse metadata from filenames when embedded tags are missing.
# Uses $Token markup (see the Settings page for the full list of parseable tokens).
filename_template = "$Author - $Title"

# Infer fields from the folder hierarchy when a book folder's depth under a scan
# path matches this scheme. Uses the same $Token markup as the filename template;
# blank disables it. Example:
# directory_scheme = "$Author/$Series/$Title"

# Organize naming. LazyLibrarian-style $Token patterns used when organizing M4Bs
# into the library, so the layout matches a LazyLibrarian library. Tokens include
# $Author $SortAuthor $Title $SortTitle $Series $SerNum $PadNum $PubYear $Narrator.
# organize_folder_pattern = "$Author/$Title"
# organize_file_pattern = "$Title"

# Confidence score (0-100) at or above which a book is marked ready
# automatically. Below it, the book is routed to review.
review_threshold = 75.0

# AAC bitrate used when transcoding MP3 sources into M4B.
transcode_bitrate = "64k"

# SQLite database location. Defaults to the standard data directory when unset.
# Changing this requires a restart.
# db_path = "/path/to/colophon.db"

# Reserved for future concurrent encoding. Not used yet.
# worker_pool_size = 4

# Port the web interface listens on.
port = 8080

# URL base path when served behind a reverse proxy, for example "/colophon".
# Leave empty to serve at the root path "/".
root_path = ""

# AudiobookShelf integration. Used to trigger a library rescan after organizing.
# audiobookshelf_url = "http://localhost:13378"
# audiobookshelf_token = "your-abs-api-token"
# audiobookshelf_library_id = "your-library-id"

# Hardcover is now provided through abs-agg (set HARDCOVER_TOKEN on the abs-agg
# side); the legacy hardcover_api_token below is unused.
# hardcover_api_token = "your-hardcover-token"

# abs-agg metadata aggregator (https://github.com/Vito0912/abs-agg). Set its base
# URL to auto-discover and enable its providers.
# abs_agg_url = "http://localhost:3000"

# Real-Debrid acquisition. Set a private API token to enable the Acquire page.
# real_debrid_token = "your-rd-private-token"
# Folder downloaded files land in before ingest. Defaults to <data dir>/downloads.
# real_debrid_download_dir = "/path/to/downloads"
"""


def ensure_config_file(path: Path | None = None) -> bool:
    """Write a commented default config file if none exists.

    Returns True if a file was created, False if one was already present.
    The generated file's active keys load back to the same values as Config()."""
    path = path or default_config_path()
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return True
