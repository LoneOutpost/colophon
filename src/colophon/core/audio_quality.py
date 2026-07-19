"""Pure helpers for audio technical quality: a friendly format label, a per-file
readout, a per-book summary, and the mixed-quality finding. No I/O — callers pass in
`SourceFile`s already read by the scanner."""

from __future__ import annotations

from colophon.core.models import SourceFile

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


# Coarse bitrate tiers (kbps upper bounds); files within one tier are "the same" quality,
# which tolerates VBR jitter (125-130 kbps all land in the 128 tier).
_BITRATE_TIERS = (64, 96, 128, 192, 256)


def _bitrate_tier(bitrate: int) -> int:
    """The tier index for a bitrate in bits/s; files in the same tier count as equal quality.
    Snaps to the nearest 8 kbps first so VBR jitter (e.g. 125-130 kbps) lands in the same tier."""
    kbps = round(bitrate / 1000 / 8) * 8
    for i, ceiling in enumerate(_BITRATE_TIERS):
        if kbps <= ceiling:
            return i
    return len(_BITRATE_TIERS)


def _channels_label(channels: int) -> str:
    return {1: "mono", 2: "stereo"}.get(channels, f"{channels}ch") if channels else ""


def _khz(sample_rate: int) -> str:
    return f"{sample_rate / 1000:g} kHz" if sample_rate else ""


def format_file_quality(sf: SourceFile) -> str:
    """A compact per-file readout, e.g. '128 kbps · 44.1 kHz · stereo · MP3'. Unknown parts are
    omitted; all-unknown returns ''."""
    parts = [
        f"{round(sf.bitrate / 1000)} kbps" if sf.bitrate else "",
        _khz(sf.sample_rate),
        _channels_label(sf.channels),
        sf.codec,
    ]
    return " · ".join(p for p in parts if p)


def _audio_with_quality(source_files: list[SourceFile]) -> list[SourceFile]:
    """The source files that carry a known bitrate (0 = unknown / not yet re-scanned)."""
    return [sf for sf in source_files if sf.bitrate > 0]


def book_quality_summary(source_files: list[SourceFile]) -> str | None:
    """A one-line quality badge for a book: the shared quality when its known files agree, else
    'Mixed quality', or None when no file has known quality."""
    known = _audio_with_quality(source_files)
    if not known:
        return None
    first = known[0]
    uniform = all(
        _bitrate_tier(sf.bitrate) == _bitrate_tier(first.bitrate)
        and sf.codec == first.codec
        and sf.sample_rate == first.sample_rate
        and sf.channels == first.channels
        for sf in known
    )
    if not uniform:
        return "Mixed quality"
    kbps = round(first.bitrate / 1000)
    return f"{kbps} kbps {first.codec}".strip()
