from colophon.core.errors import ColophonError, TagWriteError


def test_tag_write_error_is_a_colophon_error():
    err = TagWriteError("boom")
    assert isinstance(err, ColophonError)
    assert str(err) == "boom"
