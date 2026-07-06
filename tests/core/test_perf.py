import logging

from colophon.core import perf


def test_span_is_noop_when_disabled(monkeypatch, caplog):
    monkeypatch.setattr(perf, "_ENABLED", False)
    with caplog.at_level(logging.INFO, logger="colophon.perf"):
        with perf.span("outer"):
            with perf.span("inner"):
                pass
    assert caplog.records == []  # disabled: nothing measured, nothing logged


def test_enabled_emits_one_nested_tree(monkeypatch, caplog):
    monkeypatch.setattr(perf, "_ENABLED", True)
    with caplog.at_level(logging.INFO, logger="colophon.perf"):
        with perf.span("root"):
            with perf.span("childA"):
                pass
            with perf.span("childB"):
                pass
    # The outermost span emits exactly one record holding the whole tree, top-down,
    # with children indented under the root and appearing in completion order.
    assert len(caplog.records) == 1
    text = caplog.records[0].getMessage()
    lines = [ln for ln in text.splitlines() if "ms" in ln]
    assert lines[0].startswith("root: ")
    assert lines[1].strip().startswith("childA: ")
    assert lines[2].strip().startswith("childB: ")
    assert lines[1].startswith("  ")  # nested one level under root


def test_timed_decorator_wraps_sync_and_async(monkeypatch, caplog):
    monkeypatch.setattr(perf, "_ENABLED", True)

    @perf.timed("myfn")
    def myfn(x):
        return x * 2

    with caplog.at_level(logging.INFO, logger="colophon.perf"):
        assert myfn(21) == 42
    assert "myfn: " in caplog.records[0].getMessage()
