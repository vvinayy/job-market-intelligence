"""
Database layer — cleans and writes scraped postings.

Kept separate from the scraper so each file does one job: the scraper
knows how to read web pages, cleaning.py knows how to turn one scraped
record into a cleaned one, this knows how to talk to Postgres. There is
no raw_postings table — every record is cleaned in-process, right here,
before it's ever written.

Connection settings come from environment variables so no password ends
up committed in a file:

    PowerShell:
        $env:PGPASSWORD="your-password"
        $env:PGDATABASE="jobmarket"     (optional, defaults to jobmarket)
        $env:PGUSER="postgres"          (optional, defaults to postgres)
        $env:PGHOST="localhost"         (optional)
        $env:PGPORT="5432"              (optional)
"""

import os
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

from cleaning import clean_record


def get_connection():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
    )


# ON CONFLICT is where dedup actually happens. If this fingerprint is
# already in the table, refresh it in place instead of inserting a
# second row. Every field refreshes except job_id and first_seen_date —
# postings get edited after they go live (a salary gets added, a
# description gets reworded, a listing moves to a new URL), so a
# narrower SET clause would silently go stale on every repeat sighting.
UPSERT_SQL = """
INSERT INTO cleaned_postings (
    fingerprint, url, title, company, description,
    experience_min, experience_max, salary_min, salary_max,
    city_ids, unmapped_locations, working_type, employment_type, contract_type,
    role_family, role_category, department, industry_type,
    posted_date, posted_raw, openings
) VALUES %s
ON CONFLICT (fingerprint) DO UPDATE SET
    url                = EXCLUDED.url,
    title              = EXCLUDED.title,
    company            = EXCLUDED.company,
    description        = EXCLUDED.description,
    experience_min     = EXCLUDED.experience_min,
    experience_max     = EXCLUDED.experience_max,
    salary_min         = EXCLUDED.salary_min,
    salary_max         = EXCLUDED.salary_max,
    city_ids           = EXCLUDED.city_ids,
    unmapped_locations = EXCLUDED.unmapped_locations,
    working_type       = EXCLUDED.working_type,
    employment_type    = EXCLUDED.employment_type,
    contract_type      = EXCLUDED.contract_type,
    role_family        = EXCLUDED.role_family,
    role_category      = EXCLUDED.role_category,
    department         = EXCLUDED.department,
    industry_type      = EXCLUDED.industry_type,
    posted_date        = EXCLUDED.posted_date,
    posted_raw         = EXCLUDED.posted_raw,
    openings           = EXCLUDED.openings,
    last_seen_date     = CURRENT_DATE,
    times_seen         = cleaned_postings.times_seen + 1
RETURNING job_id, fingerprint, (xmax = 0) AS was_inserted;
"""


def save_records(records: list[dict]) -> tuple[int, int]:
    """Clean and write scraped postings — cleaned_postings, plus
    posting_skills, posting_qualifications and posting_cities for
    whatever job_ids this batch actually touched.

    Returns (new_count, repeat_count) so the run can report how much
    was genuinely new versus already known."""
    if not records:
        return 0, 0

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT city_id, city_name FROM cities")
            city_name_to_id = {row["city_name"]: row["city_id"] for row in cur.fetchall()}

        cleaned = [clean_record(r, city_name_to_id) for r in records]

        # Postgres can't ON CONFLICT DO UPDATE the same row twice inside
        # one statement, so duplicates have to be removed BEFORE the
        # insert. Naukri does list the same job more than once on a
        # single search page, so this fires in practice.
        seen_fingerprints = set()
        deduped = []
        skipped_in_batch = 0
        for c in cleaned:
            fp = c["posting"]["fingerprint"]
            if fp in seen_fingerprints:
                skipped_in_batch += 1
                continue
            seen_fingerprints.add(fp)
            deduped.append(c)

        if skipped_in_batch:
            print(f"  ({skipped_in_batch} duplicate posting(s) within this batch collapsed into one)")

        rows = [
            (
                c["posting"]["fingerprint"], c["posting"]["url"], c["posting"]["title"],
                c["posting"]["company"], c["posting"]["description"],
                c["posting"]["experience_min"], c["posting"]["experience_max"],
                c["posting"]["salary_min"], c["posting"]["salary_max"],
                c["posting"]["city_ids"], c["posting"]["unmapped_locations"],
                c["posting"]["working_type"], c["posting"]["employment_type"],
                c["posting"]["contract_type"], c["posting"]["role_family"],
                c["posting"]["role_category"], c["posting"]["department"],
                c["posting"]["industry_type"], c["posting"]["posted_date"],
                c["posting"]["posted_raw"], c["posting"]["openings"],
            )
            for c in deduped
        ]

        with conn.cursor() as cur:
            results = execute_values(cur, UPSERT_SQL, rows, fetch=True)
            # Looked up by fingerprint rather than trusting result order
            # to match input order — true in practice for a single
            # multi-row INSERT, but this doesn't rely on it either way.
            fingerprint_to_job_id = {row[1]: row[0] for row in results}
            new_count = sum(1 for row in results if row[2])
            repeat_count = len(results) - new_count

            skill_rows, qualification_rows, city_rows, job_ids_touched = [], [], [], []

            for c in deduped:
                job_id = fingerprint_to_job_id[c["posting"]["fingerprint"]]
                job_ids_touched.append(job_id)
                skill_rows += [(job_id, s["skill"], s["category"]) for s in c["skills"]]
                qualification_rows += [(job_id, q["level"], q["field_of_study"]) for q in c["qualifications"]]
                city_rows += [(job_id, city_id) for city_id in c["posting"]["city_ids"]]

            # Rebuild each touched posting's skills/qualifications/cities —
            # cheap at this scale, and avoids stale rows when a posting's
            # skills or location change between scrapes.
            if job_ids_touched:
                cur.execute("DELETE FROM posting_skills WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_qualifications WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_cities WHERE job_id = ANY(%s)", (job_ids_touched,))

            if skill_rows:
                execute_values(cur, "INSERT INTO posting_skills (job_id, skill, category) VALUES %s", skill_rows)
            if qualification_rows:
                execute_values(cur, "INSERT INTO posting_qualifications (job_id, level, field_of_study) VALUES %s", qualification_rows)
            if city_rows:
                execute_values(cur, "INSERT INTO posting_cities (job_id, city_id) VALUES %s ON CONFLICT DO NOTHING", city_rows)

        conn.commit()
        return new_count, repeat_count
    finally:
        conn.close()
