"""Exercises liveness_checker.main() end to end against the real database.

Separate from test_liveness.py, which is pure and always runs. This one needs
Postgres and skips itself without it, like test_api does.

It exists because of a bug the pure tests could not have caught: _log_expired
worked perfectly when called directly, but main() closed the connection in a
`finally` before reaching it, so every real run that found an expiry died with
"connection already closed". The function was tested; its position in main()
was not. That is exactly the seam this file covers.

Nothing here writes: _apply is stubbed out and the HTTP check is replaced, so
the database is only ever read.
"""

import pytest

psycopg2 = pytest.importorskip("psycopg2")

import liveness_checker  # noqa: E402


@pytest.fixture
def real_targets(monkeypatch):
    """Three real (job_id, url) pairs, fed in regardless of what is actually
    due today.

    The work queue is deliberately bypassed: whether postings are due depends
    on whether the checker has already run today, and a test that only
    exercises main() on some days is not a regression test. Real job_ids are
    used so _log_expired's lookup returns actual rows."""
    try:
        conn = liveness_checker._connect()
    except psycopg2.Error:
        pytest.skip("database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job_id, url FROM cleaned_postings ORDER BY job_id LIMIT 3")
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        pytest.skip("no postings collected yet")
    monkeypatch.setattr(liveness_checker, "due_for_check", lambda conn, limit: rows)
    return rows


@pytest.fixture
def no_writes_no_toasts(monkeypatch):
    """Neutralise every side effect: no UPDATE, no notification, no sleeping."""
    applied = {}

    def fake_apply(conn, expired, live):
        applied["expired"] = list(expired)
        applied["live"] = list(live)

    monkeypatch.setattr(liveness_checker, "_apply", fake_apply)
    monkeypatch.setattr(liveness_checker.notify, "toast", lambda *a, **k: True)
    monkeypatch.setattr(liveness_checker.time, "sleep", lambda *_: None)
    return applied


def test_main_can_log_expired_postings_without_a_closed_connection(
    real_targets, no_writes_no_toasts, monkeypatch
):
    """The regression. Every checked posting comes back expired, which forces
    main() through _log_expired -- the call that needs a live connection."""
    monkeypatch.setattr(liveness_checker, "check_one", lambda client, url: "expired")

    # Bypass the >30% safety valve, which would otherwise abort before the
    # logging is ever reached. Under 20 postings it does not engage.
    rc = liveness_checker.main(limit=3)

    assert rc == 0, "a run that found expiries should succeed, not crash"
    assert no_writes_no_toasts.get("expired"), "expected the stub to receive job_ids"


def test_main_survives_a_run_with_no_expiries(
    real_targets, no_writes_no_toasts, monkeypatch
):
    """The other branch: _log_expired returns early on an empty list, so the
    connection must still be closed cleanly and the run still succeed."""
    monkeypatch.setattr(liveness_checker, "check_one", lambda client, url: "live")

    assert liveness_checker.main(limit=3) == 0
    assert no_writes_no_toasts.get("expired") == []


def test_main_writes_nothing_when_every_check_is_inconclusive(
    real_targets, no_writes_no_toasts, monkeypatch
):
    """An unknown verdict is not evidence of anything and must reach neither
    list -- the property that stops a Naukri block writing off the table."""
    monkeypatch.setattr(liveness_checker, "check_one", lambda client, url: "unknown")

    liveness_checker.main(limit=3)
    assert no_writes_no_toasts.get("expired") == []
    assert no_writes_no_toasts.get("live") == []
