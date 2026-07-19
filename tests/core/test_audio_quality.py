from colophon.core.audio_quality import codec_label


def test_codec_label_maps_common_extensions():
    assert codec_label("mp3") == "MP3"
    assert codec_label("m4b") == "M4B"
    assert codec_label("m4a") == "AAC"
    assert codec_label("flac") == "FLAC"
    assert codec_label("opus") == "Opus"
    assert codec_label("ogg") == "OGG"


def test_codec_label_unknown_extension_uppercases():
    assert codec_label("wma") == "WMA"
    assert codec_label("") == ""
