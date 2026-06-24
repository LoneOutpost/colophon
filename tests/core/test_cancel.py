def test_cancel_token_flips():
    from colophon.core.cancel import CancelToken

    t = CancelToken()
    assert t.cancelled is False
    t.cancel()
    assert t.cancelled is True


def test_controller_reexports_cancel_token():
    from colophon.controller import CancelToken as C1
    from colophon.core.cancel import CancelToken as C2

    assert C1 is C2
