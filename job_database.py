"""
Database layer — fingerprinting and inserting scraped postings.

Kept separate from the scraper so each file does one job: the scraper
knows how to read web pages, this knows how to talk to Postgres.

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
import hashlib
import psycopg2
from psycopg2.extras import execute_values


def get_connection():
    """Open a connection using environment variables.

    psycopg2 reads PGPASSWORD, PGUSER, PGHOST and PGPORT from the
    environment automatically, but being explicit here makes the
    defaults visible rather than hidden."""
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
    )


def normalize(text: str | None) -> str:
    """Lowercase and collapse whitespace, so 'Acme  Corp ' and
    'acme corp' produce the same fingerprint. Without this, trivial
    formatting differences would look like different jobs."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def make_fingerprint(company: str, title: str, location: str, experience: str = "") -> str:
    """Turn the identifying fields into one short code.

    Same values always produce the same code, on any machine, every
    time — that predictability is what makes it usable as a 'have I
    seen this job before?' check.

    Experience is included deliberately. Large employers post many
    openings sharing a title and city (Accenture had 15+ 'Custom
    Software Engineer' roles in Hyderabad), and without experience in
    the mix they all collapse into one row and real postings are lost.
    A genuine repost keeps the same experience range, so repost
    detection still works."""
    combined = (
        f"{normalize(company)}|{normalize(title)}"
        f"|{normalize(location)}|{normalize(experience)}"
    )
    return hashlib.sha256(combined.encode()).hexdigest()


# ON CONFLICT is where dedup actually happens. If this fingerprint is
# already in the table, don't insert a second row — refresh it instead.
#
# Every field except identity (job_id, fingerprint) and first_seen_date
# gets overwritten with the latest scrape's values. Naukri postings get
# edited after they go live — a salary gets added, a description gets
# reworded, a posting gets relisted under a new URL — and a narrower
# SET clause would freeze all of that at whatever was true the first
# time this fingerprint was ever seen, silently going stale on every
# repeat sighting after.
INSERT_SQL = """
INSERT INTO raw_postings (
    url, fingerprint, title, company, experience, location,
    key_skills, tech_in_desc, employment_type, working_type, salary,
    posted_date, posted_raw, openings, description
) VALUES %s
ON CONFLICT (fingerprint) DO UPDATE
    SET url              = EXCLUDED.url,
        title             = EXCLUDED.title,
        company           = EXCLUDED.company,
        experience        = EXCLUDED.experience,
        location          = EXCLUDED.location,
        key_skills        = EXCLUDED.key_skills,
        tech_in_desc      = EXCLUDED.tech_in_desc,
        employment_type   = EXCLUDED.employment_type,
        working_type      = EXCLUDED.working_type,
        salary            = EXCLUDED.salary,
        posted_date       = EXCLUDED.posted_date,
        posted_raw        = EXCLUDED.posted_raw,
        openings          = EXCLUDED.openings,
        description       = EXCLUDED.description,
        last_seen_date    = CURRENT_DATE,
        times_seen        = raw_postings.times_seen + 1
RETURNING (xmax = 0) AS was_inserted;
"""


def save_records(records: list[dict]) -> tuple[int, int]:
    """Write scraped postings to raw_postings.

    Returns (new_count, repeat_count) so the run can report how much
    was genuinely new versus already known."""
    if not records:
        return 0, 0

    rows = []
    seen_fingerprints = set()
    skipped_in_batch = 0

    for r in records:
        # Fields that may be absent (tech_in_description is omitted when
        # empty) or the "not found" placeholder — store NULL instead, so
        # the database holds a real absence rather than a magic string.
        def clean(value):
            return None if value in (None, "not found", "") else value

        fingerprint = make_fingerprint(
            r.get("company", ""),
            r.get("title", ""),
            r.get("location", ""),
            r.get("experience", ""),
        )

        # Postgres can't ON CONFLICT DO UPDATE the same row twice inside
        # one statement, so duplicates have to be removed BEFORE the
        # insert. Naukri does list the same job more than once on a
        # single search page, so this fires in practice.
        if fingerprint in seen_fingerprints:
            skipped_in_batch += 1
            continue
        seen_fingerprints.add(fingerprint)

        rows.append((
            r.get("url"),
            fingerprint,
            clean(r.get("title")),
            clean(r.get("company")),
            clean(r.get("experience")),
            clean(r.get("location")),
            r.get("key_skills") if isinstance(r.get("key_skills"), list) else None,
            r.get("tech_in_description") if isinstance(r.get("tech_in_description"), list) else None,
            clean(r.get("employment_type")),
            clean(r.get("working_type")),
            clean(r.get("salary")),
            clean(r.get("posted_date")),   # already an ISO date string, or None
            clean(r.get("posted_raw")),
            clean(r.get("openings")),
            clean(r.get("description")),
        ))

    if skipped_in_batch:
        print(f"  ({skipped_in_batch} duplicate posting(s) within this batch collapsed into one)")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            results = execute_values(cur, INSERT_SQL, rows, fetch=True)
            # xmax = 0 means the row was newly inserted rather than updated.
            new_count = sum(1 for row in results if row[0])
            repeat_count = len(results) - new_count
        conn.commit()
        return new_count, repeat_count
    finally:
        conn.close()
