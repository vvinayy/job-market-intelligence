"""Rebuild past reopenings -- closures a later scrape overturned -- from the logs.

Before 2026-09-30 a search re-finding a closed posting cleared its expiry and
left nothing behind. The only surviving evidence is in logs/:

  liveness_*.log  every "newly expired" line: job_id, the URL that was closed,
                  and (from the block header) when that check ran.
  scrape_*.log    every "[detail i/n] <url>" line, under a "Run started" header.

A reopening happened when a job_id was closed and then EITHER is open now, OR
was closed again later (it must have reopened in between). reopened_on is the
first scrape run after the closing check that shows the posting's later URL.

LIMIT: scrape logs print only the first 90 characters of a URL, which cuts off
Naukri's numeric job id -- so a relisting under the same slug matches the old
link's prefix too. After a closure the old link redirects as expired and no
search serves it, so a matching sighting is taken to be the posting's return.
A reopening with no matching sighting is reported and NOT written: a date that
cannot be found is not invented.

Run migrations/2026-09-30-posting-reopenings.sql first.

    python migrations/2026-09-30-backfill-posting-reopenings.py           # dry run
    python migrations/2026-09-30-backfill-posting-reopenings.py --apply
"""

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from job_database import get_connection  # noqa: E402

LOGS = Path(__file__).resolve().parent.parent / "logs"
STAMP = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")
CLOSED = re.compile(r"^\s{4,}(\d+)\s{2,}\S.*1st seen.*?(https://\S+)")
DETAIL = re.compile(r"^\[detail \d+/\d+\] (\S+?)(?:\.\.\.)?$")
PREFIX = 90   # naukri_collector.py prints url[:90]


def _stamp(line: str) -> datetime | None:
    m = STAMP.search(line)
    if not m:
        return None
    mo, d, y, h, mi, s = map(int, m.groups())
    return datetime(y, mo, d, h, mi, s)


def closures() -> dict[int, list[tuple[datetime, str]]]:
    out: dict[int, list[tuple[datetime, str]]] = {}
    for f in sorted(LOGS.glob("liveness_*.log")):
        # No header -> assume end of day, the latest a check could have run.
        when = datetime.strptime(f.stem.split("_")[1], "%Y-%m-%d").replace(hour=23, minute=59)
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Check started"):
                when = _stamp(line) or when
            elif m := CLOSED.match(line):
                out.setdefault(int(m[1]), []).append((when, m[2]))
    return {k: sorted(v) for k, v in out.items()}


def sightings() -> list[tuple[datetime, str]]:
    out = []
    for f in sorted(LOGS.glob("scrape_*.log")):
        when = datetime.strptime(f.stem.split("_")[1], "%Y-%m-%d")
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Run started"):
                when = _stamp(line) or when
            elif m := DETAIL.match(line):
                out.append((when, m[1][:PREFIX]))
    return sorted(out)


def first_sighting_after(seen, after: datetime, url: str) -> datetime | None:
    prefix = url[:PREFIX]
    return next((t for t, u in seen if t > after and u == prefix), None)


def main(apply: bool) -> None:
    closed, seen = closures(), sightings()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT job_id, url, is_expired FROM posting_state WHERE job_id = ANY(%s)",
                        (list(closed),))
            state = {j: (u, e) for j, u, e in cur.fetchall()}

            rows, unfound = [], []
            for job_id, events in closed.items():
                if job_id not in state:
                    continue
                url_now, expired_now = state[job_id]
                # Each closure is followed either by a later closure (so it
                # reopened in between) or, for the last one, by being open now.
                later = [(when, url) for when, url in events[1:]]
                if not expired_now:
                    later.append((None, url_now))
                for (closed_at, old_url), (_, new_url) in zip(events, later):
                    back = first_sighting_after(seen, closed_at, new_url)
                    if back is None:
                        unfound.append((job_id, closed_at.date(), new_url))
                        continue
                    rows.append((job_id, back.date(), closed_at.date(), "observed",
                                 old_url, new_url, "log_backfill"))

            print(f"{len(rows)} reopening(s) rebuilt, {len(unfound)} without a findable date")
            for r in rows:
                kind = "same link" if r[4] == r[5] else "relisted"
                print(f"  job {r[0]:>5}  closed {r[2]}  back {r[1]}  ({kind})")
            for j, d, u in unfound:
                print(f"  job {j:>5}  closed {d}  NOT WRITTEN -- no later sighting of {u[:70]}...")

            if apply and rows:
                cur.executemany("""
                    INSERT INTO posting_reopenings
                        (job_id, reopened_on, closed_on, expiry_basis, old_url, new_url, recorded_from)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id, reopened_on) DO NOTHING""", rows)
                conn.commit()
                print("applied")
            elif not apply:
                print("dry run -- pass --apply to write")
    finally:
        conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
