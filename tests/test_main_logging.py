import logging

from colophon.__main__ import configure_logging, raise_ws_message_cap


def test_default_level_is_info(monkeypatch):
    monkeypatch.delenv("COLOPHON_LOG_LEVEL", raising=False)
    assert configure_logging() == logging.INFO
    assert logging.getLogger("colophon").level == logging.INFO


def test_env_var_enables_debug(monkeypatch):
    monkeypatch.setenv("COLOPHON_LOG_LEVEL", "debug")
    assert configure_logging() == logging.DEBUG
    assert logging.getLogger("colophon.services.ingest").isEnabledFor(logging.DEBUG)


def test_debug_is_scoped_to_colophon_not_third_parties(monkeypatch):
    # COLOPHON_LOG_LEVEL=DEBUG must not put noisy libraries (httpcore, httpx) at
    # DEBUG — only the colophon tree honors the level.
    monkeypatch.setenv("COLOPHON_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger("colophon").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("httpx").isEnabledFor(logging.DEBUG)


def test_unknown_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("COLOPHON_LOG_LEVEL", "bogus")
    assert configure_logging() == logging.INFO


def test_explicit_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("COLOPHON_LOG_LEVEL", "DEBUG")
    assert configure_logging("WARNING") == logging.WARNING


def test_raise_ws_message_cap_lifts_engineio_buffer(monkeypatch):
    # A large acquire session can exceed socket.io's 1 MB default; the cap must be lifted so the
    # message goes through instead of dropping the connection.
    from nicegui import core

    class _Eio:
        max_http_buffer_size = 1_000_000

    class _Sio:
        eio = _Eio()

    fake = _Sio()
    monkeypatch.setattr(core, "sio", fake)
    raise_ws_message_cap(5_000_000)
    assert fake.eio.max_http_buffer_size == 5_000_000


def test_raise_ws_message_cap_noops_before_server_built(monkeypatch):
    from nicegui import core

    monkeypatch.setattr(core, "sio", None)
    raise_ws_message_cap()  # must not raise when the socket server isn't created yet
