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
    db_path: Path | None = None             # None => default user-data location
    filename_template: str = "%author% - %title%"
    library_root: Path | None = None        # destination root for organized M4Bs
    audiobookshelf_url: str | None = None
    audiobookshelf_token: str | None = None
    audiobookshelf_library_id: str | None = None
    lazylibrarian_url: str | None = None
    lazylibrarian_api_key: str | None = None
    hardcover_api_token: str | None = None


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
