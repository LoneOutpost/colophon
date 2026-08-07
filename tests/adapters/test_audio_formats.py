def test_format_for_maps_extensions_to_handlers():
    from colophon.adapters.audio_formats import (
        Mp3Format,
        Mp4Format,
        VorbisFormat,
        format_for,
    )
    assert isinstance(format_for(".mp3"), Mp3Format)
    assert isinstance(format_for(".M4B"), Mp4Format)     # case-insensitive
    assert isinstance(format_for(".opus"), VorbisFormat)
    assert isinstance(format_for(".flac"), VorbisFormat)
    assert format_for(".wav") is None                    # unknown -> None
