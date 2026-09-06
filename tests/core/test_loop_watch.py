import asyncio
import time

from colophon.core.loop_watch import LoopStall, start_loop_watch


async def _settle(seconds: float) -> None:
    """Yield to the loop repeatedly so the watchdog's ping can be serviced."""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        await asyncio.sleep(0.01)


def _block_the_loop_for_a_while() -> None:
    """A deliberately synchronous call, named so the captured stack can be asserted on."""
    time.sleep(0.4)


async def test_a_blocking_call_is_reported_with_how_long_and_where():
    stalls: list[LoopStall] = []
    stop = start_loop_watch(interval=0.02, threshold=0.05, on_stall=stalls.append)
    try:
        await _settle(0.1)          # responsive: nothing to report yet
        assert stalls == []
        _block_the_loop_for_a_while()
        await _settle(0.2)          # let the watchdog finish its measurement
    finally:
        stop()

    assert stalls, "a blocked event loop went unreported"
    stall = stalls[0]
    assert stall.seconds >= 0.3
    # The stack is captured while the loop is still blocked, so it names the culprit.
    assert any("_block_the_loop_for_a_while" in frame for frame in stall.stack), stall.stack


async def test_a_responsive_loop_reports_nothing():
    stalls: list[LoopStall] = []
    stop = start_loop_watch(interval=0.02, threshold=0.05, on_stall=stalls.append)
    try:
        await _settle(0.3)
    finally:
        stop()
    assert stalls == []


async def test_stop_ends_the_watch():
    stalls: list[LoopStall] = []
    stop = start_loop_watch(interval=0.02, threshold=0.05, on_stall=stalls.append)
    await _settle(0.05)
    stop()
    _block_the_loop_for_a_while()   # after stopping, a stall must go unnoticed
    await _settle(0.2)
    assert stalls == []


async def test_stall_renders_a_readable_one_line_summary():
    stalls: list[LoopStall] = []
    stop = start_loop_watch(interval=0.02, threshold=0.05, on_stall=stalls.append)
    try:
        _block_the_loop_for_a_while()
        await _settle(0.2)
    finally:
        stop()
    assert stalls
    text = str(stalls[0])
    assert "event loop blocked" in text
    assert "s" in text  # carries the duration
