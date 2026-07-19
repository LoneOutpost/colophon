"""Pure helpers for audio technical quality: a friendly format label, a per-file
readout, a per-book summary, and the mixed-quality finding. No I/O — callers pass in
`SourceFile`s already read by the scanner."""

from __future__ import annotations

# Friendly container/format labels by bare (lowercased, dotless) extension.
_CODEC_LABELS = {
    "mp3": "MP3",
    "m4b": "M4B",
    "m4a": "AAC",
    "aac": "AAC",
    "flac": "FLAC",
    "opus": "Opus",
    "ogg": "OGG",
    "oga": "OGG",
    "wav": "WAV",
}


def codec_label(ext: str) -> str:
    """A friendly format label for a bare extension (e.g. 'mp3' -> 'MP3', 'm4b' -> 'M4B').
    Unknown non-empty extensions are upper-cased; empty stays empty."""
    ext = ext.lower().lstrip(".")
    if not ext:
        return ""
    return _CODEC_LABELS.get(ext, ext.upper())
