"""Decides whether a posting URL is still live, from what the browser saw.

Naukri soft-redirects an expired posting to a search page carrying
`expJD=true`. HTTP status stays 200 either way, so the status code is no
help -- the redirect is the whole signal. Verified against all 495 stored
URLs: 62 expired, 0 errors, 0 pages fitting neither state.

Pure functions only, so the rule can be tested without a browser. The
Playwright side lives in liveness_checker.py.

The one rule this file exists to enforce: expiry needs positive evidence.
A timeout, a block page or a renamed selector is "unknown", never
"expired" -- otherwise one Naukri block writes off the whole table, and
afterwards that is indistinguishable from a real market event.
"""

from urllib.parse import urlparse, parse_qs

# Naukri's own marker on the redirect target. Read as "expired JD".
EXPIRED_MARKER = "expjd"

# Above this share of a run coming back expired, assume a block or a layout
# change rather than a mass extinction. The census measured 12.5% expired
# cumulatively across a 19-day window, so a single day near 30% is well
# outside anything real. A guess, but a generously wide one.
IMPLAUSIBLE_EXPIRY_RATE = 0.30

# Below this many pages the rate is meaningless -- don't abort a smoke test.
MIN_RUN_FOR_RATE_CHECK = 20


def _same_posting(requested: str, final: str) -> bool:
    """True when the browser stayed on the requested posting.

    Compares path only: Naukri appends tracking params (`?src=jobsearchDesk`)
    on some entry paths without that meaning anything changed.
    """
    return urlparse(requested).path.rstrip("/") == urlparse(final).path.rstrip("/")


def _has_expiry_marker(final: str) -> bool:
    """True only when Naukri actually said expired.

    Parses the query rather than substring-matching, so `expJD=false` and a
    stray `expjd` inside a slug don't count.
    """
    params = parse_qs(urlparse(final).query)
    for key, values in params.items():
        if key.lower() == EXPIRED_MARKER:
            return any(v.strip().lower() == "true" for v in values)
    return False


def classify(requested_url: str, final_url: str | None, has_description: bool) -> str:
    """'expired', 'live', or 'unknown'.

    'unknown' writes nothing -- the same outcome as not having checked,
    which is what it is.
    """
    if not final_url:
        return "unknown"
    if _has_expiry_marker(final_url) and not _same_posting(requested_url, final_url):
        return "expired"
    if _same_posting(requested_url, final_url) and has_description:
        return "live"
    return "unknown"


def classify_response(requested_url: str, status: int, location: str | None) -> str:
    """Same rule, from a bare HTTP response instead of a rendered page.

    Naukri answers an expired posting with a 302 whose Location carries
    expJD=true, so no DOM is needed to tell the two states apart -- measured
    at 40/40 against a browser census. A HEAD is ~8x faster than rendering
    and never downloads a body.

    Anything unexpected (403, 429, 5xx, a redirect somewhere else) is
    "unknown", so a block degrades into writing nothing rather than into
    declaring the table dead.
    """
    if status in (301, 302, 303, 307, 308):
        if location and _has_expiry_marker(location):
            return "expired"
        return "unknown"
    if status == 200:
        return "live"
    return "unknown"


def rate_is_implausible(expired: int, checked: int) -> bool:
    """True when a run's expiry rate means "we are blocked", not "they closed"."""
    if checked < MIN_RUN_FOR_RATE_CHECK:
        return False
    return (expired / checked) > IMPLAUSIBLE_EXPIRY_RATE
