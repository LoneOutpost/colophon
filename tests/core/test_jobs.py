from colophon.core.jobs import JobRegistry


def test_track_registers_and_clears():
    reg = JobRegistry()
    assert reg.active() == []
    with reg.track("Re-probe durations") as job:
        assert [j.label for j in reg.active()] == ["Re-probe durations"]
        job.progress(3, 10, "Some Book")
        (live,) = reg.active()
        assert live.done == 3 and live.total == 10 and live.detail == "Some Book"
        assert abs(live.fraction - 0.3) < 1e-9
    assert reg.active() == []  # cleared on exit


def test_active_is_a_snapshot_not_live_reference():
    reg = JobRegistry()
    with reg.track("Scan") as job:
        job.progress(1, 5, "")
        snap = reg.active()[0]
        job.progress(4, 5, "")
        assert snap.done == 1  # the earlier snapshot is unaffected by later updates
        assert reg.active()[0].done == 4


def test_fraction_is_none_without_total():
    reg = JobRegistry()
    with reg.track("Downloading") as job:
        job.progress(2, None, "connecting")
        assert reg.active()[0].fraction is None


def test_job_cleared_even_when_body_raises():
    reg = JobRegistry()
    try:
        with reg.track("Encode + organize"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert reg.active() == []


def test_multiple_jobs_ordered_oldest_first():
    reg = JobRegistry()
    with reg.track("Scan"), reg.track("Re-probe durations"):
        assert [j.label for j in reg.active()] == ["Scan", "Re-probe durations"]
