"""Application configuration, persisted as TOML under the XDG config dir."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from platformdirs import user_config_path
from pydantic import BaseModel


class Config(BaseModel):
    scan_paths: list[Path] = []
    lazylibrarian_config_ini: Path | None = None
    review_threshold: float = 75.0
    transcode_bitrate: str = "64k"
    worker_pool_size: int | None = None  # None => default to cpu_count - 1
    port: int = 8080
    root_path: str = ""  # URL base path behind a reverse proxy; "" serves at "/"
    db_path: Path | None = None             # None => default user-data location
    filename_template: str = "%author% - %title%"
    saved_filename_patterns: list[str] = []  # reusable patterns offered in the parse-from-filename modal
    directory_scheme: str = ""  # e.g. "Author/Series/Title"; "" disables directory inference
    library_root: Path | None = None        # destination root for organized M4Bs
    audiobookshelf_url: str | None = None
    audiobookshelf_token: str | None = None
    audiobookshelf_library_id: str | None = None
    lazylibrarian_url: str | None = None
    lazylibrarian_api_key: str | None = None
    hardcover_api_token: str | None = None
    real_debrid_token: str | None = None
    real_debrid_download_dir: Path | None = None  # None => <data dir>/downloads
    genre_mapping: dict[str, str] = {}
    accepted_genres: list[str] = []
    genre_whitelist_enabled: bool = False


def default_config_path() -> Path:
    return user_config_path("colophon") / "config.toml"


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


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

# Path to your LazyLibrarian config.ini. Colophon reads the audiobook folder
# and file naming patterns from it so output matches your LazyLibrarian layout.
# lazylibrarian_config_ini = "/path/to/lazylibrarian/config.ini"

# Template used to parse metadata from filenames when embedded tags are missing.
filename_template = "%author% - %title%"

# Infer author/series/title from the folder hierarchy when a book folder's depth
# under a scan path matches this scheme. Empty disables it. Example:
# directory_scheme = "Author/Series/Title"

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

# LazyLibrarian integration. Read-only status lookups.
# lazylibrarian_url = "http://localhost:5299"
# lazylibrarian_api_key = "your-ll-api-key"

# Hardcover metadata source. Set a token to enable it.
# hardcover_api_token = "your-hardcover-token"

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
