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
from psycopg2.extras import execute_values, RealDictCursor, Json

from cleaning import clean_record, categorize_skill


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
    fingerprint, url, title, company, description, description_hash,
    responsibilities_text, requirements_text,
    experience_min, experience_max, salary_min, salary_max,
    city_ids, unmapped_locations, working_type, employment_type, contract_type,
    role_family, seniority_level, role_category, naukri_role, industry_type, department,
    posted_date, posted_raw, openings, applicant_count, applicant_count_qualifier,
    company_rating, company_reviews, company_badges, source_search, certifications
) VALUES %s
ON CONFLICT (fingerprint) DO UPDATE SET
    url                   = EXCLUDED.url,
    title                 = EXCLUDED.title,
    company               = EXCLUDED.company,
    description           = EXCLUDED.description,
    description_hash      = EXCLUDED.description_hash,
    responsibilities_text = EXCLUDED.responsibilities_text,
    requirements_text     = EXCLUDED.requirements_text,
    experience_min        = EXCLUDED.experience_min,
    experience_max        = EXCLUDED.experience_max,
    salary_min            = EXCLUDED.salary_min,
    salary_max            = EXCLUDED.salary_max,
    city_ids              = EXCLUDED.city_ids,
    unmapped_locations    = EXCLUDED.unmapped_locations,
    working_type          = EXCLUDED.working_type,
    employment_type       = EXCLUDED.employment_type,
    contract_type         = EXCLUDED.contract_type,
    role_family           = EXCLUDED.role_family,
    seniority_level       = EXCLUDED.seniority_level,
    role_category         = EXCLUDED.role_category,
    naukri_role           = EXCLUDED.naukri_role,
    industry_type         = EXCLUDED.industry_type,
    department            = EXCLUDED.department,
    posted_date           = EXCLUDED.posted_date,
    posted_raw            = EXCLUDED.posted_raw,
    openings              = EXCLUDED.openings,
    applicant_count       = EXCLUDED.applicant_count,
    applicant_count_qualifier = EXCLUDED.applicant_count_qualifier,
    company_rating        = EXCLUDED.company_rating,
    company_reviews       = EXCLUDED.company_reviews,
    company_badges        = EXCLUDED.company_badges,
    source_search         = EXCLUDED.source_search,
    certifications        = EXCLUDED.certifications,
    last_seen_date        = CURRENT_DATE,
    times_seen            = cleaned_postings.times_seen + 1
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

        # Resolve every skill name in this batch to a skill_id, auto-
        # registering any name not already in the dictionary. Category is
        # only set at registration time (an initial guess from cleaning.py's
        # SKILL_CATEGORIES, or NULL if that dict doesn't know it) — once a
        # skill exists, this never touches its category again, so a manual
        # correction made directly in the skills table persists across
        # future scrapes instead of being overwritten.
        all_skill_names = sorted({s for c in deduped for s in c["skills"]})
        skill_name_to_id = {}
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if all_skill_names:
                cur.execute("SELECT skill_id, skill_name FROM skills WHERE skill_name = ANY(%s)", (all_skill_names,))
                skill_name_to_id = {row["skill_name"]: row["skill_id"] for row in cur.fetchall()}

        new_skill_names = [s for s in all_skill_names if s not in skill_name_to_id]
        if new_skill_names:
            new_skill_rows = [(name, categorize_skill(name)) for name in new_skill_names]
            with conn.cursor() as cur:
                inserted = execute_values(
                    cur,
                    "INSERT INTO skills (skill_name, category) VALUES %s RETURNING skill_id, skill_name",
                    new_skill_rows, fetch=True,
                )
            skill_name_to_id.update({name: skill_id for skill_id, name in inserted})

        rows = [
            (
                c["posting"]["fingerprint"], c["posting"]["url"], c["posting"]["title"],
                c["posting"]["company"], c["posting"]["description"], c["posting"]["description_hash"],
                c["posting"]["responsibilities_text"], c["posting"]["requirements_text"],
                c["posting"]["experience_min"], c["posting"]["experience_max"],
                c["posting"]["salary_min"], c["posting"]["salary_max"],
                c["posting"]["city_ids"], c["posting"]["unmapped_locations"],
                c["posting"]["working_type"], c["posting"]["employment_type"],
                c["posting"]["contract_type"], c["posting"]["role_family"], c["posting"]["seniority_level"],
                c["posting"]["role_category"], c["posting"]["naukri_role"],
                c["posting"]["industry_type"], c["posting"]["department"],
                c["posting"]["posted_date"],
                c["posting"]["posted_raw"], c["posting"]["openings"],
                c["posting"]["applicant_count"], c["posting"]["applicant_count_qualifier"],
                c["posting"]["company_rating"], c["posting"]["company_reviews"], c["posting"]["company_badges"],
                c["posting"]["source_search"],
                c["posting"]["certifications"],
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
                # posting_skills is one row per posting now (skill_ids is an
                # array), so every touched posting gets exactly one row here,
                # even one with no skills — unlike qualifications/cities below,
                # which stay one-row-per-entry and simply contribute zero rows
                # when a posting has none. preferred_skill_ids is always a
                # subset of skill_ids (enforced by a CHECK constraint too) —
                # cleaning.py already guarantees this by construction.
                skill_ids = sorted(skill_name_to_id[s] for s in c["skills"])
                preferred_skill_ids = sorted(skill_name_to_id[s] for s in c["preferred_skills"])
                skill_rows.append((job_id, skill_ids, preferred_skill_ids))
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
                execute_values(cur, "INSERT INTO posting_skills (job_id, skill_ids, preferred_skill_ids) VALUES %s", skill_rows)
            if qualification_rows:
                execute_values(cur, "INSERT INTO posting_qualifications (job_id, level, field_of_study) VALUES %s", qualification_rows)
            if city_rows:
                execute_values(cur, "INSERT INTO posting_cities (job_id, city_id) VALUES %s ON CONFLICT DO NOTHING", city_rows)

        conn.commit()
        return new_count, repeat_count
    finally:
        conn.close()


# =====================================================================
# SCRAPE HEALTH — makes a broken selector or a storage failure visible
# in the run's own log output, instead of only showing up later as a
# gap someone happens to notice by hand (how Department/Industry Type
# and the applicant-count bug were actually found, both times).
# =====================================================================
def record_scrape_run(
    search_url: str, started_at, finished_at,
    postings_found: int, postings_scraped: int, postings_written: int,
    field_found_counts: dict[str, int], storage_ok: bool, error_message: str | None,
) -> int:
    """Logs one naukri_collector.py run. Returns the new run_id so the
    caller can immediately check it against recent history."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO scrape_runs (
                    search_url, started_at, finished_at, postings_found,
                    postings_scraped, postings_written, field_found_counts,
                    storage_ok, error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_id
            """, (search_url, started_at, finished_at, postings_found,
                  postings_scraped, postings_written, Json(field_found_counts),
                  storage_ok, error_message))
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    finally:
        conn.close()


def check_field_health(current_run_id: int, lookback_runs: int = 10) -> list[dict]:
    """Compares this run's per-field found-rate against the average of
    the last `lookback_runs` completed runs. Flags a field only when it
    historically had a real presence (avg_rate > 30% — otherwise a
    naturally-sparse field like salary would flag on every run) AND
    this run's rate fell to under half that average — a real drop, not
    ordinary day-to-day noise.

    Returns a list of {field, current_rate, historical_avg_rate} dicts,
    empty when nothing looks wrong."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT field_found_counts, postings_scraped
                FROM scrape_runs
                WHERE run_id != %s AND field_found_counts IS NOT NULL AND postings_scraped > 0
                ORDER BY started_at DESC LIMIT %s
            """, (current_run_id, lookback_runs))
            history = cur.fetchall()

            cur.execute("""
                SELECT field_found_counts, postings_scraped
                FROM scrape_runs WHERE run_id = %s
            """, (current_run_id,))
            current = cur.fetchone()
    finally:
        conn.close()

    if not history or not current or not current["postings_scraped"]:
        return []

    historical_rates: dict[str, list[float]] = {}
    for row in history:
        scraped = row["postings_scraped"] or 0
        if not scraped:
            continue
        for field, count in (row["field_found_counts"] or {}).items():
            historical_rates.setdefault(field, []).append(count / scraped)

    current_scraped = current["postings_scraped"]
    warnings = []
    for field, count in (current["field_found_counts"] or {}).items():
        rates = historical_rates.get(field)
        if not rates:
            continue
        avg_rate = sum(rates) / len(rates)
        current_rate = count / current_scraped
        if avg_rate > 0.3 and current_rate < avg_rate * 0.5:
            warnings.append({"field": field, "current_rate": round(current_rate, 3),
                              "historical_avg_rate": round(avg_rate, 3)})
    return warnings
