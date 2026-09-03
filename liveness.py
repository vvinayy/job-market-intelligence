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

import re
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


# ---------------------------------------------------------------------
# hirist — a different signal entirely, not a variation on the Naukri one.
#
# Measured 2026-09-02: hirist carries NOTHING at the HTTP layer. HEAD and GET
# both answer 200 with no Location for live and expired postings alike, and
# the "expired" page is drawn in JavaScript after the shell loads. A checker
# built on status codes would read every hirist posting as alive forever.
#
# The signal is `hasExpired` in the detail API hirist_collector.py already
# calls. Census the same day: 20 stored postings all false, 20 ids sampled
# from the old range all true, and a clean cutoff by job id -- everything
# below ~1,626,000 expired, everything above ~1,635,000 live, which is what a
# fixed listing period produces. Re-observed 2026-09-03 over 60 stored
# postings, all live.
#
# What none of that shows is a posting CROSSING between the two states, which
# is the thing a checker actually depends on -- a field that merely tracked
# age would separate those populations just as cleanly. That is why nothing
# here is wired into liveness_checker.py yet: hirist_liveness_probe.py
# records the verdict daily without writing it, and the first flip promotes
# the rule.
#
# Do NOT reach for `status` or `active` in that payload. 8 of those 20 expired
# postings still reported active = 1, so that field would have been wrong 40%
# of the time while looking authoritative -- the same shape as the old
# working_type fallback.
# ---------------------------------------------------------------------

# The job code is the trailing number: .../j/<slug>-1667157 and, on the .com
# domain, .../j/<slug>-1439211.html. Anchored at the end because slugs carry
# their own digits ("...-3-5-yrs-1667157").
_HIRIST_CODE_RE = re.compile(r"-(\d+)(?:\.html)?/?$")


def hirist_job_code(url: str | None) -> str | None:
    """The detail-API key for a hirist posting URL, or None if it has none."""
    if not url:
        return None
    match = _HIRIST_CODE_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def classify_hirist(status: int, payload: dict | None) -> str:
    """'expired', 'live' or 'unknown' from one detail-API response.

    Same discipline as the Naukri rule: only positive evidence counts. A 404
    means the job code names nothing, which is not the same as a posting that
    expired -- and a payload without `hasExpired` is unknown rather than live,
    so a change to the API's shape degrades into writing nothing.
    """
    if status != 200 or not isinstance(payload, dict):
        return "unknown"
    data = payload.get("data")
    if not isinstance(data, dict):
        return "unknown"
    expired = data.get("hasExpired")
    if expired is True:
        return "expired"
    if expired is False:
        return "live"
    return "unknown"


def rate_is_implausible(expired: int, checked: int) -> bool:
    """True when a run's expiry rate means "we are blocked", not "they closed"."""
    if checked < MIN_RUN_FOR_RATE_CHECK:
        return False
    return (expired / checked) > IMPLAUSIBLE_EXPIRY_RATE
