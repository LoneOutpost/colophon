import httpx
import pytest

from colophon.adapters.realdebrid import RealDebridClient, RealDebridError


def _client(handler) -> RealDebridClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.real-debrid.com/rest/1.0")
    return RealDebridClient("tok", client=http)


@pytest.fixture(autouse=True)
def _fast_pacer(monkeypatch):
    # The real request pacing would make these MockTransport tests slow; zero it and reset the
    # shared pacer's adaptive state so tests don't accumulate spacing/backoff across the session.
    import colophon.adapters.realdebrid as rd
    monkeypatch.setattr(rd._RD_PACER, "min_interval", 0.0)
    rd._RD_PACER.interval = 0.0
    rd._RD_PACER._next_at = 0.0
    rd._RD_PACER._pause_until = 0.0


async def test_unrestrict_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json={"filename": "a.mp3", "filesize": 5, "download": "http://d/a"})

    client = _client(handler)
    unr = await client.unrestrict_link("L1")
    assert unr.filename == "a.mp3"
    assert calls["n"] == 2  # retried once


async def test_unrestrict_does_not_retry_on_404():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "unknown link"})

    client = _client(handler)
    with pytest.raises(RealDebridError) as ei:
        await client.unrestrict_link("L1")
    assert ei.value.status_code == 404
    assert calls["n"] == 1  # not retried


async def test_unrestrict_exhausts_retries_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "unavailable"})

    client = _client(handler)
    with pytest.raises(RealDebridError) as ei:
        await client.unrestrict_link("L1")
    assert ei.value.status_code == 503
    assert calls["n"] == 8  # stop_after_attempt(8): 8 tries then reraise


async def test_torrent_info_and_unrestrict_accept_force_kwarg():
    def handler(request: httpx.Request) -> httpx.Response:
        if "unrestrict" in str(request.url):
            return httpx.Response(200, json={"filename": "a.mp3", "filesize": 5, "download": "http://d/a"})
        return httpx.Response(200, json={"id": "t1", "filename": "Bk", "status": "downloaded",
                                         "links": [], "files": []})

    client = _client(handler)
    info = await client.torrent_info("t1", force=True)
    assert info.id == "t1"
    unr = await client.unrestrict_link("L1", force=True)
    assert unr.filename == "a.mp3"


async def test_pacer_spaces_request_starts():
    from colophon.adapters.realdebrid import _Pacer

    clock = {"t": 1000.0}
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)
        clock["t"] += d  # a real sleep advances the clock

    pacer = _Pacer(0.5, now=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(4):
        await pacer.wait()
    # First call is immediate (delay 0, no sleep); each later call is spaced by the interval.
    assert delays == [0.5, 0.5, 0.5]


async def test_request_goes_through_the_global_pacer(monkeypatch):
    import colophon.adapters.realdebrid as rd

    calls = {"n": 0}

    async def spy() -> None:
        calls["n"] += 1

    monkeypatch.setattr(rd._RD_PACER, "wait", spy)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "t", "filename": "", "status": "downloaded",
                                         "links": [], "files": []})

    client = _client(handler)
    await client.torrent_info("t")
    assert calls["n"] == 1  # the request was paced


def test_pacer_backs_off_multiplicatively_and_recovers_additively():
    from colophon.adapters.realdebrid import _Pacer

    async def _noop(_d: float) -> None:
        pass

    p = _Pacer(0.5, max_interval=4.0, backoff=2.0, recovery=0.5, now=lambda: 0.0, sleep=_noop)
    assert p.interval == 0.5
    p.on_throttle()
    assert p.interval == 1.0  # doubled
    p.on_throttle()
    assert p.interval == 2.0
    p.on_throttle()
    p.on_throttle()
    assert p.interval == 4.0  # capped at max_interval
    p.on_success()
    assert p.interval == 3.5  # additive step down
    for _ in range(100):
        p.on_success()
    assert p.interval == 0.5  # never below the floor


async def test_pacer_honors_retry_after_as_a_global_pause():
    from colophon.adapters.realdebrid import _Pacer

    clock = {"t": 100.0}
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)
        clock["t"] += d

    # min_interval 0 so only the Retry-After pause (not spacing) drives the delay.
    p = _Pacer(0.0, now=lambda: clock["t"], sleep=fake_sleep)
    p.on_throttle(retry_after=5.0)  # RD asked us to wait 5s: pause the whole fleet until then
    await p.wait()
    assert delays == [5.0]
    await p.wait()
    assert delays == [5.0]  # pause already elapsed; no further wait


async def test_request_reports_throttle_and_success_to_the_pacer(monkeypatch):
    import colophon.adapters.realdebrid as rd

    events: list = []
    monkeypatch.setattr(rd._RD_PACER, "on_throttle", lambda ra=None: events.append(("throttle", ra)))
    monkeypatch.setattr(rd._RD_PACER, "on_success", lambda: events.append(("success",)))

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "slow down"})
        return httpx.Response(200, json={"filename": "a.mp3", "filesize": 5, "download": "http://d/a"})

    client = _client(handler)
    await client.unrestrict_link("L1")
    assert ("throttle", 2.0) in events  # the 429 fed Retry-After back to the pacer
    assert ("success",) in events       # the eventual 200 eased the rate back
