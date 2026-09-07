from colophon.core.errors import describe


def test_a_message_carrying_exception_reads_type_then_message():
    assert describe(ValueError("bad input")) == "ValueError: bad input"


def test_a_wordless_exception_still_names_its_type():
    # mutagen.id3.error() on a corrupt MP3 carries no message; "failed: " with nothing after it is
    # what sent a real diagnosis down a dead end.
    from mutagen.id3 import error as ID3Error

    assert describe(ID3Error()) == "error"


def test_whitespace_only_messages_are_treated_as_empty():
    assert describe(RuntimeError("   ")) == "RuntimeError"
