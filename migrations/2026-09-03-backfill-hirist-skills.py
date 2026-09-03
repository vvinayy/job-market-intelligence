"""Re-derive skills for the hirist postings extracted before the detector was
rewritten.

The 20 hirist postings first seen 2026-09-02 were scraped partway through the
session that also rewrote extract_skills() and added fourteen vocabulary
entries (96ada74). They therefore carry the OLD vocabulary: measured against
today's detector they are missing 2-4 skills each -- NoSQL, LLM, S3, Data
Ingestion, Data Modeling, Data Pipeline, IICS. Everything scraped since is
correct, so this is a one-off population, not an ongoing drift.

Run migrations/2026-09-03-canonicalise-iics-spelling.sql FIRST. Without it
this registers 'IICS' beside the existing 'Iics' and splits that skill across
two ids, which is the defect JPA already has.

WHAT IT TOUCHES: skill_ids, preferred_skill_ids and skill_groups, on
cleaned_postings and posting_skills. Nothing else.

WHAT IT DELIBERATELY DOES NOT TOUCH: last_seen_date and times_seen. Those mean
"a search surfaced this posting", and a backfill is not a sighting -- writing
them would inflate coverage with a re-derivation, the same conflation
liveness_checker.py refuses to make. applicant_count is left alone for the
same reason; it drifts on its own and refreshes on the next real scrape.

skill_daily_counts is not touched either. Those rows are observations of what
the pipeline saw on the day, and 2026-09-02 genuinely saw the old vocabulary.
Rewriting them would be inventing a measurement nobody took.

    python migrations/2026-09-03-backfill-hirist-skills.py            # dry run
    python migrations/2026-09-03-backfill-hirist-skills.py --apply
"""

import os
import random
import sys
import time

import httpx
import psycopg2
from psycopg2.extras import Json, execute_values

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cleaning                      # noqa: E402
import hirist_collector as hc        # noqa: E402
import liveness                      # noqa: E402

TARGET_FIRST_SEEN = "2026-09-02"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def connect():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
    )


def main(apply: bool) -> int:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT city_name, city_id FROM cities")
        city_map = dict(cur.fetchall())
        cur.execute(
            """SELECT job_id, url,
                      (SELECT array_agg(s.skill_name ORDER BY s.skill_name) FROM skills s
                        WHERE s.skill_id = ANY(c.skill_ids || skill_group_ids(c.skill_groups)))
                 FROM cleaned_postings c
                WHERE c.source = 'hirist' AND c.first_seen_date = %s
                ORDER BY job_id""",
            (TARGET_FIRST_SEEN,),
        )
        rows = cur.fetchall()

    if not rows:
        print("Nothing to backfill.")
        conn.close()
        return 0

    print(f"{len(rows)} posting(s) first seen {TARGET_FIRST_SEEN}.\n")
    plans, gained_total = [], 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        for job_id, url, stored_names in rows:
            code = liveness.hirist_job_code(url)
            try:
                data = client.get("https://gladiator.hirist.tech/job/detail",
                                  params={"jobcode": code}).json().get("data")
            except Exception as exc:
                print(f"  {job_id}: SKIPPED ({type(exc).__name__})")
                continue
            if not isinstance(data, dict):
                print(f"  {job_id}: SKIPPED (no payload)")
                continue

            fresh = cleaning.clean_record(hc.build_record(data), city_name_to_id=city_map)
            was, now = set(stored_names or []), set(fresh["skills"])
            gained, lost = sorted(now - was), sorted(was - now)
            gained_total += len(gained)
            plans.append((job_id, fresh))
            if gained or lost:
                print(f"  {job_id}: +{len(gained)} {gained}"
                      + (f"  -{len(lost)} {lost}" if lost else ""))
            time.sleep(random.uniform(2.0, 3.5))

    print(f"\n{len(plans)} posting(s) resolved, {gained_total} skill mentions gained.")
    if not apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        conn.close()
        return 0

    try:
        with conn:
            with conn.cursor() as cur:
                # Register any skill name the current vocabulary produces that
                # the table has never seen. Same INSERT the writer uses.
                names = sorted({s for _, f in plans for s in f["skills"]})
                cur.execute("SELECT skill_name, skill_id FROM skills WHERE skill_name = ANY(%s)",
                            (names,))
                name_to_id = dict(cur.fetchall())
                missing = [n for n in names if n not in name_to_id]
                if missing:
                    execute_values(
                        cur,
                        "INSERT INTO skills (skill_name, category) VALUES %s "
                        "RETURNING skill_id, skill_name",
                        [(n, cleaning.categorize_skill(n)) for n in missing],
                        fetch=True)
                    name_to_id.update({n: i for i, n in cur.fetchall()})
                    print(f"registered {len(missing)} new skill(s): {missing}")

                for job_id, fresh in plans:
                    groups = [sorted({name_to_id[s] for s in g if s in name_to_id})
                              for g in fresh.get("skill_choice_groups", [])]
                    groups = [g for g in groups if len(g) > 1]
                    grouped = {i for g in groups for i in g}
                    outright = sorted(name_to_id[s] for s in fresh["skills"]
                                      if name_to_id[s] not in grouped)
                    # hirist publishes no preferred signal -- see
                    # 2026-09-03-clear-hirist-preferred-skills.sql
                    cur.execute(
                        """UPDATE cleaned_postings
                              SET skill_ids = %s, preferred_skill_ids = '{}',
                                  skill_groups = %s
                            WHERE job_id = %s""",
                        (outright, Json(groups), job_id))
                    cur.execute(
                        """INSERT INTO posting_skills
                                  (job_id, skill_ids, preferred_skill_ids, skill_groups)
                           VALUES (%s, %s, '{}', %s)
                           ON CONFLICT (job_id) DO UPDATE
                              SET skill_ids = EXCLUDED.skill_ids,
                                  preferred_skill_ids = EXCLUDED.preferred_skill_ids,
                                  skill_groups = EXCLUDED.skill_groups""",
                        (job_id, outright, Json(groups)))

                # The two copies of this fact must not diverge.
                cur.execute(
                    """SELECT COUNT(*) FROM cleaned_postings c
                         JOIN posting_skills ps ON ps.job_id = c.job_id
                        WHERE c.source = 'hirist'
                          AND (c.skill_ids <> ps.skill_ids
                               OR c.skill_groups <> ps.skill_groups)""")
                divergent = cur.fetchone()[0]
                if divergent:
                    raise RuntimeError(
                        f"{divergent} hirist row(s) disagree between "
                        f"cleaned_postings and posting_skills -- rolling back")
    except Exception as exc:
        print(f"FAILED, nothing written: {exc}")
        conn.close()
        return 1

    print(f"Applied to {len(plans)} posting(s).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
