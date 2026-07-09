from colophon.ui.batch_log import BatchItem, BatchLog


def _log() -> BatchLog:
    return BatchLog([BatchItem("a", "A"), BatchItem("b", "B")])


def test_progress_line_hidden_until_driven():
    log = _log()
    assert log._progress_row.visible is False


def test_set_progress_shows_counts_and_failures():
    log = _log()
    log.set_progress(1, 2, failed=0)
    assert log._progress_row.visible is True
    assert log._progress.text == "Processing 1 of 2"
    assert log._progress_fail.text == ""  # no failure suffix while none have failed

    log.set_progress(2, 2, failed=1)
    assert log._progress.text == "Processing 2 of 2"
    assert "1 failed" in log._progress_fail.text


def test_finish_hides_the_live_progress_line():
    # The end-of-run summary in the action row supersedes the live counter.
    log = _log()
    log.set_progress(2, 2, failed=1)
    log.finish("done", on_close=lambda: None)
    assert log._progress_row.visible is False


def test_failed_rows_expose_ids_for_retry():
    log = _log()
    log.update("a", "failed: output name collision", kind="fail")
    log.update("b", "skipped: no source files", kind="skip")
    assert log.failed_ids() == ["a"]  # skipped books are not retried, only failures
    assert log.counts() == {"fail": 1, "skip": 1}
