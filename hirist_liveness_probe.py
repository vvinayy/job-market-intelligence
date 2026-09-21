"""Observes what a hirist liveness check would conclude, without writing it.

NO LONGER RUN NIGHTLY, AND THE RULE IT TESTS WILL NOT BE PROMOTED. It was
built to earn one piece of evidence the 2026-09-02 census could not give: a
single posting observed crossing from live to expired. That census compared
two *populations* -- 20 scraped yesterday against 20 job ids from 2021 -- and
warned that a field which merely tracks age would split them just as cleanly.

On 2026-09-08 that suspicion was confirmed directly, and this script did not
have to wait for it. Sampling job codes from 2019 to 2026 and binary-searching
the boundary put the flip at exactly 150 days after createdTime: 148 days
False, 150 days True, no exception in 41 samples. `hasExpired` is a clock.

That also explains five silent nights. 3-7 September recorded 60, 83, 185, 185
and 188 observations, every one of them `live`, plus a single `unknown` on the
7th which was an HTTP 504 correctly refusing to call a gateway timeout a
death. Nothing could have flipped: no stored hirist posting is anywhere near
150 days old, and when one does reach that age it will flip because of the
calendar, not because the job closed.

The 150 days are now acted on, but as a delisting rather than a closure:
liveness_checker.mark_delisted_hirist() does the subtraction from posted_date
and records expiry_basis = 'delisted', which /analytics/closures excludes. That
needs no network, so this script is not on the path to it.

Kept, not deleted, for two reasons. The observation table is a real record of
what the API said on those days, and running this by hand around 2026-11-07 --
when the oldest stored posted_date, 10 June 2026, crosses day 150 -- would
confirm the prediction against hirist's own flag rather than against our
arithmetic. It still never writes cleaned_postings.

    python hirist_liveness_probe.py             # observe every hirist posting
    python hirist_liveness_probe.py --limit 5   # smoke test

IT NEVER WRITES cleaned_postings. Not is_expired, not expired_on, not
last_checked_on. That is the whole point of it existing separately from
liveness_checker.py rather than as a flag on it -- a flag can be flipped by
accident, a missing UPDATE statement cannot. Until a transition is observed,
those 20 rows stay NULL, which says "never checked" and is the truth.

Why the API and not the URL: hirist carries no HTTP signal at all. HEAD and
GET both answer 200 with no Location for live and expired postings alike, and
the "expired" page is drawn in JavaScript after the shell loads. Measured
2026-09-02 over both states.
"""

import random
import sys
import time
from datetime import datetime

import httpx
import psycopg2

import liveness
from job_database import connection_params

# The same endpoint hirist_collector.py already reads postings from. Deliberately
# not re-declared there and imported here: this script must keep working if the
# collector is mid-edit, and one constant is cheaper than that coupling.
DETAIL_API = "https://gladiator.hirist.tech/job/detail"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Slower than liveness_checker's 0.8-1.5s. That paces a HEAD against Naukri,
# an infrastructure built for search-engine traffic; this is a JSON API behind
# an app, and there are 20 rows to get through, so there is nothing to gain
# from hurrying.
THROTTLE_MIN, THROTTLE_MAX = 2.0, 4.0

REQUEST_TIMEOUT = 30


def _connect():
    return psycopg2.connect(**connection_params())


def targets(conn, limit: int | None) -> list[tuple[int, str]]:
    """Every hirist posting, including ones a previous run called expired.

    Deliberately NOT filtered to is_expired IS NOT TRUE the way the real
    checker's queue is. Nothing here writes is_expired, so there is no such
    state to skip -- and a posting whose verdict flips back would itself be
    evidence, about the rule rather than the posting.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT job_id, url
              -- url and source both live in posting_state since the
              -- 2026-09-15 split. This still writes nothing here.
              FROM posting_state
             WHERE source = 'hirist'
             ORDER BY job_id
             LIMIT %s
            """,
            (limit,),                      # NULL limit means no limit in SQL
        )
        return cur.fetchall()


def observe_one(client: httpx.Client, url: str) -> tuple[str | None, int | None, bool | None, str]:
    """(job_code, http_status, has_expired, verdict) for one posting."""
    code = liveness.hirist_job_code(url)
    if not code:
        return None, None, None, "unknown"
    try:
        resp = client.get(DETAIL_API, params={"jobcode": code})
    except httpx.HTTPError:
        return code, None, None, "unknown"   # a failed check is not a death

    try:
        payload = resp.json()
    except ValueError:
        payload = None

    verdict = liveness.classify_hirist(resp.status_code, payload)
    has_expired = None
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        raw = payload["data"].get("hasExpired")
        has_expired = raw if isinstance(raw, bool) else None
    return code, resp.status_code, has_expired, verdict


def record(conn, rows: list[tuple]) -> None:
    """One row per posting per day. Re-running today overwrites today, so a
    second run is a correction rather than a duplicate."""
    with conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO hirist_liveness_observations
                   (observed_on, job_id, job_code, http_status, has_expired, verdict)
            VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
            ON CONFLICT (observed_on, job_id) DO UPDATE
                SET job_code    = EXCLUDED.job_code,
                    http_status = EXCLUDED.http_status,
                    has_expired = EXCLUDED.has_expired,
                    verdict     = EXCLUDED.verdict
            """,
            rows,
        )


def report_transitions(conn) -> int:
    """Postings whose verdict differs from the last time they were observed.

    This is the entire reason the table exists. Until one of these appears,
    the rule has only ever been tested on two separate populations.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH ranked AS (
                SELECT job_id, observed_on, verdict,
                       LAG(verdict)     OVER w AS previous_verdict,
                       LAG(observed_on) OVER w AS previous_on
                  FROM hirist_liveness_observations
                WINDOW w AS (PARTITION BY job_id ORDER BY observed_on)
            )
            SELECT r.job_id, r.previous_on, r.previous_verdict,
                   r.observed_on, r.verdict,
                   COALESCE(c.company, '?'), COALESCE(c.title, '?')
              FROM ranked r
              JOIN cleaned_postings c ON c.job_id = r.job_id
             WHERE r.previous_verdict IS NOT NULL
               AND r.previous_verdict <> r.verdict
             ORDER BY r.observed_on DESC, r.job_id
            """
        )
        rows = cur.fetchall()

    if not rows:
        return 0
    print(f"\n[probe] {len(rows)} VERDICT CHANGE(S) observed:")
    for job_id, prev_on, prev, on, now, company, title in rows:
        print(f"    {job_id:>6}  {prev_on} {prev:<7} -> {on} {now:<7}  "
              f"{company[:26]:<26} {title[:36]}")
    return len(rows)


def main(limit: int | None) -> int:
    started = datetime.now()
    conn = _connect()
    try:
        rows_to_check = targets(conn, limit)
    except psycopg2.Error as exc:
        print(f"[probe] Could not read the work queue: {exc}")
        conn.close()
        return 1

    if not rows_to_check:
        print("[probe] No hirist postings stored.")
        conn.close()
        return 0

    print(f"[probe] Observing {len(rows_to_check)} hirist posting(s). "
          f"Nothing will be written to cleaned_postings.")

    observations, tally = [], {"expired": 0, "live": 0, "unknown": 0}
    with httpx.Client(headers={"User-Agent": USER_AGENT},
                      timeout=REQUEST_TIMEOUT) as client:
        for i, (job_id, url) in enumerate(rows_to_check, start=1):
            code, status, has_expired, verdict = observe_one(client, url)
            observations.append((job_id, code, status, has_expired, verdict))
            tally[verdict] += 1
            print(f"  {i:>3}/{len(rows_to_check)}  {job_id:>6}  "
                  f"HTTP {str(status or '-'):<4} hasExpired={str(has_expired):<5} "
                  f"-> {verdict}")
            sys.stdout.flush()
            if i < len(rows_to_check):
                time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))

    try:
        record(conn, observations)
    except psycopg2.Error as exc:
        print(f"[probe] Could not record observations: {exc}")
        conn.close()
        return 1

    changes = report_transitions(conn)
    conn.close()

    took = (datetime.now() - started).total_seconds()
    print(f"\n[probe] Done in {took:.0f}s - would have written "
          f"{tally['expired']} expired, {tally['live']} live; "
          f"{tally['unknown']} inconclusive. Wrote none of it.")
    if changes:
        print("[probe] A verdict changed. That is the transition the census "
              "could not show -- the rule is ready to promote into "
              "liveness_checker.py.")
    else:
        print("[probe] No verdict has changed yet. Run again tomorrow.")
    return 0


if __name__ == "__main__":
    limit_arg = None
    if "--limit" in sys.argv:
        try:
            limit_arg = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit needs a number, e.g. --limit 5")
            sys.exit(2)
    sys.exit(main(limit_arg))
