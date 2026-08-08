import asyncio
import logging
import threading
import time

import pytest

from colophon.core.progress import step


def _msgs(caplog):
    return [r.message for r in caplog.records if r.name == "colophon.progress"]


def test_fast_step_logs_start_and_done_without_heartbeat(caplog):
    with caplog.at_level(logging.INFO, logger="colophon.progress"):
        with step("fast op", interval=0.05):
            pass
    msgs = _msgs(caplog)
    assert msgs[0] == "fast op: starting"
    assert msgs[-1].startswith("fast op: done in ")
    assert not any("still running" in m for m in msgs)


def test_long_step_emits_at_least_one_heartbeat(caplog):
    with caplog.at_level(logging.INFO, logger="colophon.progress"):
        with step("slow op", interval=0.05):
            time.sleep(0.17)
    msgs = _msgs(caplog)
    assert any(m.startswith("slow op: still running (") for m in msgs)
    assert msgs[-1].startswith("slow op: done in ")


def test_exception_logs_failed_and_propagates(caplog):
    with caplog.at_level(logging.INFO, logger="colophon.progress"):
        with pytest.raises(ValueError):
            with step("boom op", interval=0.05):
                raise ValueError("kaboom")
    msgs = _msgs(caplog)
    assert any(m.startswith("boom op: failed after ") and "kaboom" in m for m in msgs)


def test_async_form_logs_start_and_done(caplog):
    async def go():
        async with step("async op", interval=0.05):
            await asyncio.sleep(0.12)
    with caplog.at_level(logging.INFO, logger="colophon.progress"):
        asyncio.run(go())
    msgs = _msgs(caplog)
    assert msgs[0] == "async op: starting"
    assert msgs[-1].startswith("async op: done in ")


def test_heartbeat_thread_is_joined_on_exit():
    before = threading.active_count()
    with step("t", interval=0.05):
        time.sleep(0.12)
    assert threading.active_count() == before   # no lingering heartbeat thread
