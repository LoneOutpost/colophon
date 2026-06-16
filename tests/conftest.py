"""Shared test fixtures, including an ffmpeg-backed audio file factory."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def make_audio(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a short silent audio file and returns its path.

    Usage: make_audio("01.mp3", seconds=1) -> Path. Skips the test if ffmpeg is absent.
    """

    def _make(name: str, *, seconds: int = 1) -> Path:
        if not _HAVE_FFMPEG:
            pytest.skip("ffmpeg not available")
        out = tmp_path / name
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                "-t", str(seconds), str(out),
            ],
            check=True,
        )
        return out

    return _make
