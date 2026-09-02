"""Visits stored posting URLs to find the ones Naukri has expired.

The scraper is search-driven: its whole worklist comes from
discover_job_urls(). An expired posting drops out of search results, so it is
never re-surfaced, never re-scraped, and never corrected -- the rows that most
need updating are exactly the ones the scraper cannot reach. This is the
row-driven counterpart: it reads URLs from the database instead.

Uses a bare HEAD request, not a browser. Naukri answers an expired posting
with a 302 whose Location carries expJD=true, so nothing needs rendering --
measured 40/40 against a full browser census. That makes each check ~8x
faster (0.11s vs 0.85s), downloads no page bodies at all, and removes the
interactive-logon requirement, since a headed browser was the only reason for
it.

    python liveness_checker.py                # every posting due a check
    python liveness_checker.py --limit 20     # smoke test
    python liveness_checker.py --test-notify  # preview toasts, labelled [TEST]

Every run names the postings that closed (with company, title and URL) and
the ones that came back inconclusive, so logs/liveness_<date>.log answers
"what closed today" without a query. Inconclusive rows matter most: a dead
link or the start of a block, which a bare count would hide.

Also appends one row per run to liveness_runs. That table exists for a single
number: MIN(started_at) is the date closure detection began, and every
exposure-adjusted rate depends on it -- days before it are not exposure,
because nothing was checking then. Measured against first_seen_date instead,
the denominator came out 8,280 posting-days against 1,806 real ones.

On cleaned_postings it writes only is_expired / expired_on / last_checked_on.
It never touches posting content, and never bumps last_seen_date -- that column means "a search
surfaced this", and conflating it with "we verified the URL" would destroy the
ability to tell scraper coverage from direct verification.

Detection rules and the safety valves live in liveness.py, which is pure and
tested. Every exit path raises a Windows toast via notify.py -- this runs
unattended from Task Scheduler, and an aborted run that only prints into a
log file is indistinguishable from one that never ran.
"""

import os
import random
import sys
import time
from datetime import datetime

import httpx
import psycopg2

import liveness
import notify

# A real browser UA. Naukri serves the redirect to a plain client, but an
# obviously-scripted agent is the first thing any site rate-limits.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Shorter than the scraper's 3-6s, deliberately. That figure paces full page
# renders, which pull ~40 subrequests each; this makes exactly one HEAD and
# downloads no body, so one request per second is roughly 9x gentler than
# what the scraper already does. Still randomised, still serial.
THROTTLE_MIN, THROTTLE_MAX = 0.8, 1.5

REQUEST_TIMEOUT = 30

# Above this share of a run coming back inconclusive, assume we are being
# blocked rather than that the pages changed. Distinct from the expiry-rate
# valve: that one catches "everything died", this catches "nothing answered".
IMPLAUSIBLE_UNKNOWN_RATE = 0.25


def _connect():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
    )


def due_for_check(conn, limit: int | None) -> list[tuple[int, str]]:
    """Postings needing a check: not already dead, not already done today.

    Oldest check first, so an interrupted run resumes where it mattered most
    rather than restarting from the top.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT job_id, url
              FROM cleaned_postings
             WHERE is_expired IS NOT TRUE
               AND (last_checked_on IS NULL OR last_checked_on < CURRENT_DATE)
               -- Naukri only. The whole detection rule is one measured fact --
               -- an expired posting answers with a 302 carrying expJD=true,
               -- verified 495/495 against Naukri and against nothing else. A
               -- hirist URL returns 200 whether the job is open or gone, so
               -- every one would be written as is_expired = FALSE: not merely
               -- wrong, but an invented observation that then counts as
               -- exposure in /analytics/closures and depresses every rate.
               -- hirist publishes hasExpired in its own API; until that is
               -- wired up and measured the way this rule was, leaving those
               -- rows NULL says "never checked", which is the truth.
               AND url ILIKE '%%naukri.com%%'
             ORDER BY last_checked_on ASC NULLS FIRST, job_id
             LIMIT %s
            """,
            (limit,),                      # NULL limit means no limit in SQL
        )
        return cur.fetchall()


def check_one(client: httpx.Client, url: str) -> str:
    """'expired', 'live' or 'unknown' for a single posting URL."""
    try:
        resp = client.head(url, follow_redirects=False)
    except httpx.HTTPError:
        return "unknown"                   # a failed check is not a death
    return liveness.classify_response(url, resp.status_code,
                                      resp.headers.get("location"))


def _apply(conn, expired: list[int], live: list[int]) -> None:
    with conn.cursor() as cur:
        if expired:
            cur.execute(
                """
                UPDATE cleaned_postings
                   SET is_expired = TRUE,
                       -- Only on the first confirmation: re-confirming a dead
                       -- posting must not move the date later every day.
                       expired_on = COALESCE(expired_on, CURRENT_DATE),
                       last_checked_on = CURRENT_DATE
                 WHERE job_id = ANY(%s)
                """,
                (expired,),
            )
        if live:
            cur.execute(
                """
                UPDATE cleaned_postings
                   SET is_expired = FALSE, expired_on = NULL,
                       last_checked_on = CURRENT_DATE
                 WHERE job_id = ANY(%s)
                """,
                (live,),
            )


def _log_expired(conn, expired: list[int]) -> None:
    """Name the postings that closed, and group them by role.

    A count tells you the run worked; the list is the only part anyone
    actually wants to read, and it saves querying the database to answer
    "what closed today".
    """
    if not expired:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT job_id, COALESCE(company, '?'), COALESCE(title, '?'),
                   COALESCE(role_family, 'Uncategorised'),
                   -- Days since WE first saw it, not how long Naukri listed
                   -- it: the posting predates our discovery by an unknown
                   -- amount, so this is an upper bound on observed lifetime.
                   CURRENT_DATE - first_seen_date, url
              FROM cleaned_postings
             WHERE job_id = ANY(%s)
             ORDER BY role_family NULLS LAST, company
            """,
            (expired,),
        )
        rows = cur.fetchall()

    print(f"\n[liveness] {len(rows)} newly expired:")
    for job_id, company, title, _role, days, url in rows:
        print(f"    {job_id:>6}  {company[:28]:<28}  {title[:38]:<38}  "
              f"1st seen {days:>3}d ago  {url}")

    by_role: dict[str, int] = {}
    for _, _, _, role, _, _ in rows:
        by_role[role] = by_role.get(role, 0) + 1
    print("\n[liveness] closed by role:")
    for role, n in sorted(by_role.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>4}  {role}")


def _log_unknown(unknown: list[tuple[int, str]]) -> None:
    """Name the inconclusive ones. These are the actionable rows -- a dead
    link, a changed redirect, or the start of a block -- and a bare count
    hides every one of them."""
    if not unknown:
        return
    print(f"\n[liveness] {len(unknown)} inconclusive (nothing written for these):")
    for job_id, url in unknown[:50]:
        print(f"    {job_id:>6}  {url}")
    if len(unknown) > 50:
        print(f"    ... and {len(unknown) - 50} more")


def _record_run(conn, started, checked, expired, live, unknown, aborted=None) -> None:
    """Log the run itself, separately from its results.

    MIN(started_at) over this table is the date closure detection began, and
    every exposure-adjusted rate needs it: days before it are not exposure,
    because nothing was checking and no closure could have been observed.
    Inferring it from MIN(expired_on) instead breaks the moment the oldest
    closure is deleted, and silently -- the rate just quietly inflates.

    Aborted runs are recorded too. Observation did not stop because a safety
    valve discarded that night's results; the window stayed open.

    Its own transaction, and failures here are swallowed. A run that checked
    500 postings and wrote them correctly has not failed because its bookkeeping
    row did not land.
    """
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO liveness_runs
                           (started_at, finished_at, checked, expired, live,
                            unknown, aborted_reason)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (started, datetime.now(), checked, expired, live, unknown, aborted))
    except psycopg2.Error as exc:
        print(f"[liveness] Could not record the run: {exc}")


def main(limit: int | None) -> int:
    started = datetime.now()
    conn = _connect()
    try:
        targets = due_for_check(conn, limit)
    except psycopg2.Error as exc:
        print(f"[liveness] Could not read the work queue: {exc}")
        conn.close()
        return 1

    if not targets:
        print("[liveness] Nothing due for a check.")
        notify.toast("Liveness check", "Nothing due - every posting was already checked today.")
        conn.close()
        return 0

    print(f"[liveness] {len(targets)} posting(s) to check.")
    expired: list[int] = []
    live: list[int] = []
    unknown: list[tuple[int, str]] = []

    with httpx.Client(headers={"User-Agent": USER_AGENT},
                      timeout=REQUEST_TIMEOUT) as client:
        for i, (job_id, url) in enumerate(targets, start=1):
            verdict = check_one(client, url)
            if verdict == "expired":
                expired.append(job_id)
            elif verdict == "live":
                live.append(job_id)
            else:
                unknown.append((job_id, url))

            if i % 100 == 0 or i == len(targets):
                print(f"[liveness] {i}/{len(targets)} "
                      f"- {len(expired)} expired, {len(live)} live, {len(unknown)} unknown")
                sys.stdout.flush()

            if i < len(targets):
                time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))

    decided = len(expired) + len(live)
    total = decided + len(unknown)

    if total >= liveness.MIN_RUN_FOR_RATE_CHECK and len(unknown) / total > IMPLAUSIBLE_UNKNOWN_RATE:
        print(f"[LIVENESS ABORTED] {len(unknown)} of {total} checks were inconclusive "
              f"({len(unknown)/total:.0%}). That usually means we are being blocked or "
              f"the redirect shape changed. Nothing was written.")
        _log_unknown(unknown)
        _record_run(conn, started, total, len(expired), len(live), len(unknown),
                    aborted=f"{len(unknown)}/{total} inconclusive - likely blocked "
                            f"or the redirect shape changed")
        notify.toast("Liveness check ABORTED",
                     f"{len(unknown)} of {total} checks were inconclusive "
                     f"({len(unknown)/total:.0%}). Likely blocked, or the redirect "
                     f"changed. Nothing was written.",
                     urgent=True)
        conn.close()
        return 1

    if liveness.rate_is_implausible(len(expired), decided):
        # A third of the board closing in one day does not happen; a block or a
        # layout change looks exactly like it. Write nothing and say so loudly.
        print(f"[LIVENESS ABORTED] {len(expired)} of {decided} came back expired "
              f"({len(expired)/decided:.0%}). That is implausible - assuming a block "
              f"or a layout change. Nothing was written.")
        _record_run(conn, started, total, len(expired), len(live), len(unknown),
                    aborted=f"{len(expired)}/{decided} expired - implausible; "
                            f"assumed a block or a layout change")
        notify.toast("Liveness check ABORTED",
                     f"{len(expired)} of {decided} came back expired "
                     f"({len(expired)/decided:.0%}) - implausibly many. Assuming a block "
                     f"or a layout change. Nothing was written.",
                     urgent=True)
        conn.close()
        return 1

    try:
        with conn:
            _apply(conn, expired, live)
    except psycopg2.Error as exc:
        print(f"[LIVENESS FAILED] Could not write results: {exc}")
        _record_run(conn, started, total, len(expired), len(live), len(unknown),
                    aborted=f"write failed: {exc}")
        notify.toast("Liveness check FAILED",
                     f"Checked {decided} postings but could not write the results: {exc}",
                     urgent=True)
        conn.close()
        return 1

    # The write is committed by here. _log_expired still needs the connection
    # to name the postings, so it has to run BEFORE the close -- an earlier
    # version closed in a `finally` and then logged, which raised
    # "connection already closed" on every run that found an expiry.
    #
    # Its failures are caught separately and are not fatal: the run succeeded,
    # and being unable to print a list must not report it as failed or skip
    # the notification below.
    _record_run(conn, started, total, len(expired), len(live), len(unknown))

    try:
        _log_expired(conn, expired)
    except psycopg2.Error as exc:
        print(f"[liveness] Wrote results, but could not list them: {exc}")
    finally:
        conn.close()

    _log_unknown(unknown)

    took = (datetime.now() - started).total_seconds()
    print(f"[liveness] Done in {took:.0f}s - {len(expired)} newly expired, "
          f"{len(live)} still live, {len(unknown)} inconclusive.")

    summary = f"{len(expired)} newly expired, {len(live)} still live"
    if unknown:
        summary += f", {len(unknown)} inconclusive"
    notify.toast("Liveness check done",
                 f"{summary}. Checked {total} in {took/60:.0f} min "
                 f"at {started:%H:%M}.")
    return 0


if __name__ == "__main__":
    if "--test-notify" in sys.argv:
        notify.preview()
        sys.exit(0)

    limit_arg = None
    if "--limit" in sys.argv:
        try:
            limit_arg = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit needs a number, e.g. --limit 20")
            sys.exit(2)
    sys.exit(main(limit_arg))
