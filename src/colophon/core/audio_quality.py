"""Pure helpers for audio technical quality: a friendly format label, a per-file
readout, a per-book summary, and the mixed-quality finding. No I/O — callers pass in
`SourceFile`s already read by the scanner."""

from __future__ import annotations

from colophon.core.models import Finding, FindingCode, FindingSeverity, SourceFile

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
    Snaps to the nearest 8 kbps first so VBR jitter (e.g. a 128k file reporting ~124-132 kbps)
    lands in the same tier. A rate that straddles a snap boundary can still tier apart, but that
    only yields an advisory (acknowledgeable) mixed-quality note, so it's acceptable."""
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
        sf.codec,  # already a friendly label (the scanner sets it via codec_label at capture)
    ]
    return " · ".join(p for p in parts if p)


def _audio_with_quality(source_files: list[SourceFile]) -> list[SourceFile]:
    """The source files that carry a known bitrate (0 = unknown / not yet re-scanned)."""
    return [sf for sf in source_files if sf.bitrate > 0]


def _is_uniform(known: list[SourceFile]) -> bool:
    """True when every known-quality file shares one bitrate tier, codec, sample rate, and
    channel count — i.e. they look like one edition. Assumes `known` is non-empty."""
    first = known[0]
    return all(
        _bitrate_tier(sf.bitrate) == _bitrate_tier(first.bitrate)
        and sf.codec == first.codec
        and sf.sample_rate == first.sample_rate
        and sf.channels == first.channels
        for sf in known
    )


def mixed_quality_finding(source_files: list[SourceFile]) -> Finding | None:
    """WARN when a book's known-quality audio files disagree — spanning more than one bitrate
    tier, or differing in codec, sample rate, or channels. Files with unknown quality (bitrate 0,
    not yet re-scanned) are ignored so they never false-flag. Returns None when files agree or
    fewer than two carry known quality."""
    known = _audio_with_quality(source_files)
    if len(known) < 2 or _is_uniform(known):
        return None
    codecs = sorted({sf.codec for sf in known if sf.codec})
    kbps = sorted({round(sf.bitrate / 1000) for sf in known})
    if len(codecs) > 1:
        detail = f"files mix formats ({' + '.join(codecs)})"
    else:
        detail = f"files span {kbps[0]}-{kbps[-1]} kbps"
    return Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail=detail)


def book_quality_summary(source_files: list[SourceFile]) -> str | None:
    """A one-line quality badge for a book: the shared quality when its known files agree, else
    'Mixed quality', or None when no file has known quality."""
    known = _audio_with_quality(source_files)
    if not known:
        return None
    if not _is_uniform(known):
        return "Mixed quality"
    first = known[0]
    kbps = round(first.bitrate / 1000)
    return f"{kbps} kbps {first.codec}".strip()
