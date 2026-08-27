"""Unit tests for liveness.py's pure functions. No database, no network.

The classifier is the whole safety story for expiry detection: it decides
which postings get written off as dead. The tests that matter most here are
the negative ones -- a timeout, a block page or a changed selector must come
back "unknown", never "expired", because a single Naukri block would
otherwise mass-mark live postings dead and the damage would be
indistinguishable from a real market event afterwards.
"""

import liveness


JOB_URL = "https://www.naukri.com/job-listings-software-engineer-acme-hyderabad-2-to-4-years-123456789"


# ---------------------------------------------------------------------
# Expired: Naukri soft-redirects to a search page carrying expJD=true
# ---------------------------------------------------------------------
def test_redirect_to_expjd_is_expired():
    # Real shape, taken from the census: job 27 landed here.
    final = "https://www.naukri.com/data-specialist-jobs-in-hyderabad-secunderabad?expJD=true"
    assert liveness.classify(JOB_URL, final, has_description=False) == "expired"


def test_expired_wins_even_if_a_description_somehow_rendered():
    # The redirect target is a search page; if its markup ever happens to
    # match the description selector, the redirect still decides.
    final = "https://www.naukri.com/software-engineer-jobs-in-hyderabad?expJD=true"
    assert liveness.classify(JOB_URL, final, has_description=True) == "expired"


# ---------------------------------------------------------------------
# Live: same URL, description rendered
# ---------------------------------------------------------------------
def test_same_url_with_description_is_live():
    assert liveness.classify(JOB_URL, JOB_URL, has_description=True) == "live"


def test_trailing_query_or_fragment_does_not_break_live():
    # Naukri appends tracking params on some entry paths; the posting is
    # still the posting.
    assert liveness.classify(JOB_URL, JOB_URL + "?src=jobsearchDesk", has_description=True) == "live"


# ---------------------------------------------------------------------
# Unknown: everything else. These are the tests that keep the system safe.
# ---------------------------------------------------------------------
def test_no_description_and_no_redirect_is_unknown():
    # A slow page or a renamed selector. NOT evidence of expiry.
    assert liveness.classify(JOB_URL, JOB_URL, has_description=False) == "unknown"


def test_redirect_without_expjd_is_unknown():
    # Redirected somewhere else entirely -- a login wall, a block page.
    # Plausible-looking, but Naukri never said "expired".
    final = "https://www.naukri.com/nlogin/login"
    assert liveness.classify(JOB_URL, final, has_description=False) == "unknown"


def test_homepage_redirect_is_unknown():
    assert liveness.classify(JOB_URL, "https://www.naukri.com/", has_description=False) == "unknown"


def test_empty_final_url_is_unknown():
    # goto() raised; we never learned where we ended up.
    assert liveness.classify(JOB_URL, "", has_description=False) == "unknown"
    assert liveness.classify(JOB_URL, None, has_description=False) == "unknown"


def test_expjd_false_is_not_expired():
    # Guard against substring matching on the parameter name alone.
    final = "https://www.naukri.com/software-engineer-jobs-in-hyderabad?expJD=false"
    assert liveness.classify(JOB_URL, final, has_description=False) == "unknown"


# ---------------------------------------------------------------------
# Safety valve: a run seeing implausibly many expiries is a block or a
# layout change, not a mass extinction.
# ---------------------------------------------------------------------
def test_safety_valve_allows_normal_rates():
    # Census measured 12.5% cumulative across a 19-day window, so a daily
    # run in single digits is entirely ordinary.
    assert liveness.rate_is_implausible(expired=5, checked=100) is False


def test_safety_valve_trips_on_mass_expiry():
    assert liveness.rate_is_implausible(expired=40, checked=100) is True


def test_safety_valve_ignores_tiny_runs():
    # 1 of 2 is 50%, but proves nothing -- don't abort a --limit 2 smoke test.
    assert liveness.rate_is_implausible(expired=1, checked=2) is False


def test_safety_valve_handles_zero_checked():
    assert liveness.rate_is_implausible(expired=0, checked=0) is False


# ---------------------------------------------------------------------
# The HTTP path: same rule read from a bare response. Naukri answers an
# expired posting with a 302 whose Location carries expJD=true, so a HEAD
# settles it without rendering anything.
# ---------------------------------------------------------------------
def test_redirect_with_expjd_location_is_expired():
    loc = "https://www.naukri.com/big-data-engineer-jobs-in-hyderabad?expJD=true"
    assert liveness.classify_response(JOB_URL, 302, loc) == "expired"


def test_plain_200_is_live():
    assert liveness.classify_response(JOB_URL, 200, None) == "live"


def test_redirect_elsewhere_is_unknown():
    # A login wall or a marketing page. Not evidence of expiry.
    assert liveness.classify_response(JOB_URL, 302, "https://www.naukri.com/nlogin/login") == "unknown"


def test_redirect_without_location_is_unknown():
    assert liveness.classify_response(JOB_URL, 302, None) == "unknown"


def test_block_and_error_statuses_are_unknown():
    # The failure mode that matters: being blocked must never read as a
    # market event.
    for status in (403, 429, 500, 502, 503):
        assert liveness.classify_response(JOB_URL, status, None) == "unknown"


def test_404_is_unknown_not_expired():
    # Naukri signals expiry by redirect, not by 404. A 404 is something
    # else -- a bad stored URL, say -- and guessing costs us a real row.
    assert liveness.classify_response(JOB_URL, 404, None) == "unknown"


# ---------------------------------------------------------------------
# Notification payload. No toast is actually shown here -- the XML is what
# can break, and it breaks on text nobody thought to escape.
# ---------------------------------------------------------------------
def test_toast_escapes_xml_metacharacters(monkeypatch):
    import notify

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["script"] = cmd[-1]
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    # A company name Naukri really could serve. Unescaped, the & alone
    # makes the toast XML invalid and nothing renders at all.
    assert notify.toast("Done", 'Tata & Sons <b>"x"</b>') is True
    assert "&amp;" in captured["script"]
    assert "<b>" not in captured["script"]


def test_toast_never_raises(monkeypatch):
    import notify

    def boom(cmd, **kwargs):
        raise OSError("powershell missing")

    monkeypatch.setattr(notify.subprocess, "run", boom)
    # A cosmetic failure must never fail a run that otherwise succeeded.
    assert notify.toast("Done", "all good") is False
