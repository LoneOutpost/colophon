import asyncio

from colophon.ui.dialogs import single_flight


def test_single_flight_ignores_reentrant_call_then_allows_after_completion():
    # A double-submit guard: a second call while the first is still awaiting is a no-op, so an
    # action like writing tags can't run twice; once the first finishes, it can run again.
    calls = {"n": 0}

    async def _run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler() -> None:
            calls["n"] += 1
            started.set()
            await release.wait()

        guarded = single_flight(handler)
        first = asyncio.create_task(guarded())
        await started.wait()          # first run is in flight
        await guarded()               # re-entrant click -> ignored
        assert calls["n"] == 1
        release.set()
        await first
        await guarded()               # first finished -> a new run is allowed
        assert calls["n"] == 2

    asyncio.run(_run())
