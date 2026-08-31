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


def _resolve_reference_ids(conn, table: str, id_col: str, name_col: str, names: set[str]) -> dict[str, int]:
    """Get-or-create ids for a small single-valued reference table
    (role_categories, departments, industry_types) -- same
    register-if-new pattern as skill_id resolution below, just for a
    fixed-column FK instead of an array. table/id_col/name_col are
    always one of this module's own hardcoded literals, never caller
    input, so building the SQL with an f-string here carries the same
    safety as the rest of this file's parameterised queries."""
    names = {n for n in names if n}
    if not names:
        return {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT {id_col}, {name_col} FROM {table} WHERE {name_col} = ANY(%s)", (list(names),))
        mapping = {row[name_col]: row[id_col] for row in cur.fetchall()}
    missing = [n for n in names if n not in mapping]
    if missing:
        with conn.cursor() as cur:
            inserted = execute_values(
                cur, f"INSERT INTO {table} ({name_col}) VALUES %s RETURNING {id_col}, {name_col}",
                [(n,) for n in missing], fetch=True,
            )
        mapping.update({name: ref_id for ref_id, name in inserted})
    return mapping


def _resolve_degree_ids(conn, degree_keys: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """Get-or-create ids for education_degrees, keyed by (degree_name,
    level) since the same degree_name can't collide across levels in
    practice but the table is still looked up on both."""
    degree_keys = {k for k in degree_keys if k[0]}
    if not degree_keys:
        return {}
    names = [name for name, _ in degree_keys]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT degree_id, degree_name, level FROM education_degrees WHERE degree_name = ANY(%s)", (names,))
        mapping = {(row["degree_name"], row["level"]): row["degree_id"] for row in cur.fetchall()}
    missing = [k for k in degree_keys if k not in mapping]
    if missing:
        with conn.cursor() as cur:
            inserted = execute_values(
                cur,
                "INSERT INTO education_degrees (degree_name, level) VALUES %s "
                "RETURNING degree_id, degree_name, level",
                missing, fetch=True,
            )
        mapping.update({(name, level): degree_id for degree_id, name, level in inserted})
    return mapping


def _resolve_degree_specialization_ids(conn, pairs: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Get-or-create ids for education_degree_specializations, keyed by
    (degree_id, specialization_id). This is what lets
    posting_qualification_specializations stay one row per posting like
    posting_skills: each posting just stores an array of ids into this
    dictionary instead of one row per (degree, specialization) pair, and
    the pairing itself is still fully recoverable by joining through it."""
    pairs = {p for p in pairs if None not in p}
    if not pairs:
        return {}
    degree_ids = list({d for d, _ in pairs})
    specialization_ids = list({s for _, s in pairs})
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Filtered on each column separately then intersected in Python: a %s
        # parameter can't carry composite row values. Exact, just two steps.
        cur.execute("""
            SELECT degree_specialization_id, degree_id, specialization_id
            FROM education_degree_specializations
            WHERE degree_id = ANY(%s) AND specialization_id = ANY(%s)
        """, (degree_ids, specialization_ids))
        mapping = {
            (row["degree_id"], row["specialization_id"]): row["degree_specialization_id"]
            for row in cur.fetchall()
            if (row["degree_id"], row["specialization_id"]) in pairs
        }
    missing = [p for p in pairs if p not in mapping]
    if missing:
        with conn.cursor() as cur:
            inserted = execute_values(
                cur,
                "INSERT INTO education_degree_specializations (degree_id, specialization_id) VALUES %s "
                "RETURNING degree_specialization_id, degree_id, specialization_id",
                missing, fetch=True,
            )
        mapping.update({(degree_id, spec_id): combo_id for combo_id, degree_id, spec_id in inserted})
    return mapping


def get_connection():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
    )


# ON CONFLICT is where dedup happens: a known fingerprint refreshes in place.
# Every field refreshes except job_id and first_seen_date — postings get edited
# after going live, so a narrower SET clause goes stale on repeat sightings.
UPSERT_SQL = """
INSERT INTO cleaned_postings (
    fingerprint, url, title, company, description,
    experience_min, experience_max, salary_min, salary_max,
    city_ids, unmapped_locations, working_type, is_full_time, contract_type,
    role_family, seniority_level, role_category_id, naukri_role, industry_type_id, department_id,
    posted_date, posted_raw, openings, applicant_count, applicant_count_qualifier,
    company_rating, company_reviews, company_badges, source_search, certifications,
    accepted_degree_ids, accepted_degree_specialization_ids, skill_ids, preferred_skill_ids,
    skill_groups
) VALUES %s
ON CONFLICT (fingerprint) DO UPDATE SET
    url                   = EXCLUDED.url,
    title                 = EXCLUDED.title,
    company               = EXCLUDED.company,
    description           = EXCLUDED.description,
    experience_min        = EXCLUDED.experience_min,
    experience_max        = EXCLUDED.experience_max,
    salary_min            = EXCLUDED.salary_min,
    salary_max            = EXCLUDED.salary_max,
    city_ids              = EXCLUDED.city_ids,
    unmapped_locations    = EXCLUDED.unmapped_locations,
    working_type          = EXCLUDED.working_type,
    is_full_time          = EXCLUDED.is_full_time,
    contract_type         = EXCLUDED.contract_type,
    role_family           = EXCLUDED.role_family,
    seniority_level       = EXCLUDED.seniority_level,
    role_category_id      = EXCLUDED.role_category_id,
    naukri_role           = EXCLUDED.naukri_role,
    industry_type_id      = EXCLUDED.industry_type_id,
    department_id         = EXCLUDED.department_id,
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
    accepted_degree_ids   = EXCLUDED.accepted_degree_ids,
    accepted_degree_specialization_ids = EXCLUDED.accepted_degree_specialization_ids,
    skill_ids             = EXCLUDED.skill_ids,
    preferred_skill_ids   = EXCLUDED.preferred_skill_ids,
    skill_groups          = EXCLUDED.skill_groups,
    last_seen_date        = CURRENT_DATE,
    times_seen            = cleaned_postings.times_seen + 1,
    -- Literals, not EXCLUDED: these aren't scraped fields, they follow from
    -- the scrape having succeeded. Naukri just served the page, so a
    -- previously recorded expiry is void -- and a scrape reaching a posting
    -- is a liveness check, which keeps it out of the checker's queue.
    -- A search returning this posting IS evidence it is live, so these two
    -- are honest. last_checked_on is NOT set here: that column means "the
    -- liveness checker requested this URL", and writing it from a scrape
    -- conflates coverage with verification -- the same conflation
    -- liveness_checker.py refuses to make in the other direction by never
    -- touching last_seen_date. It also made the evening run skip every
    -- posting the morning scrape had re-surfaced.
    is_expired            = FALSE,
    expired_on            = NULL
RETURNING job_id, fingerprint, (xmax = 0) AS was_inserted;
"""


def is_identifiable(posting: dict) -> bool:
    """Does this posting have enough of an identity to be worth storing?

    The fingerprint hashes company + title + location + experience, so a
    record missing all four hashes the same as every other one, and ON
    CONFLICT folds the whole batch onto a single row. Measured: 40 distinct
    postings with 40 distinct URLs went in, 1 row came out, and the run still
    reported storage_ok.

    That input is reachable. scrape_job_detail() keeps a posting whenever the
    DESCRIPTION selector renders, while title and company come from
    safe_text(), which returns None instead of raising -- deliberately, so one
    missing field cannot kill a run. A Naukri markup change hitting one
    selector and not the other produces exactly this.

    Title OR company is enough. Requiring both, or requiring location and
    experience, would discard real postings: Naukri omits those often. The bar
    is identity, not completeness.
    """
    return bool(posting.get("title") or posting.get("company"))


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
            # Read here rather than hardcoding in cleaning.py: the
            # blocklist is a data fix (a row someone adds when a generic
            # term shows up), not a code change.
            cur.execute("SELECT skill FROM skill_blocklist")
            skill_blocklist = {row["skill"] for row in cur.fetchall()}

        # Per record, not a list comprehension over the batch. clean_record can
        # raise on a malformed field, and one bad record must cost one record
        # rather than every posting scraped that run.
        cleaned = []
        malformed = 0
        for r in records:
            try:
                cleaned.append(clean_record(r, city_name_to_id, skill_blocklist))
            except Exception as exc:
                malformed += 1
                print(f"  [malformed] Skipped a record that could not be cleaned: "
                      f"{type(exc).__name__}: {exc}")
        if malformed:
            print(f"  WARNING: {malformed} of {len(records)} record(s) could not be cleaned.")

        # A record with neither title nor company has no identity, and every
        # such record shares one fingerprint -- so writing them collapses the
        # batch onto a single row instead of erroring. Drop them loudly.
        unidentifiable = [c for c in cleaned if not is_identifiable(c["posting"])]
        if unidentifiable:
            cleaned = [c for c in cleaned if is_identifiable(c["posting"])]
            print(f"  WARNING: {len(unidentifiable)} of {len(records)} record(s) had no "
                  f"title and no company, so they cannot be told apart. Not written.")
            if not cleaned:
                # Nothing survived. That is a broken selector, not a quiet day.
                print("  WARNING: NO record in this batch was identifiable. "
                      "The title/company selectors have almost certainly changed.")

        # Postgres can't ON CONFLICT DO UPDATE one row twice in a statement, so
        # dedupe first. Naukri does repeat a job on a single search page.
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

        # Get-or-create every skill name in the batch. Category is set only at
        # registration, never updated — so a manual fix in the skills table
        # survives future scrapes.
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

        # Single-valued per posting, so each is one id against its own small
        # reference table — no array, no join table.
        role_category_to_id = _resolve_reference_ids(
            conn, "role_categories", "role_category_id", "name",
            {c["posting"]["role_category"] for c in deduped})
        department_to_id = _resolve_reference_ids(
            conn, "departments", "department_id", "name",
            {c["posting"]["department"] for c in deduped})
        industry_type_to_id = _resolve_reference_ids(
            conn, "industry_types", "industry_type_id", "name",
            {c["posting"]["industry_type"] for c in deduped})

        # Resolved once per batch, before the main UPSERT, so
        # cleaned_postings.accepted_degree_ids can be written in the same
        # INSERT — duplicated onto that table for an at-a-glance read, same as
        # city_ids. The "accepted_" prefix marks OR semantics: any one degree
        # satisfies the posting, unlike skill_ids which are all wanted.
        degree_to_id = _resolve_degree_ids(
            conn,
            {(d["degree"], d["level"]) for c in deduped for d in c["qualification_degrees"]},
        )
        specialization_to_id = _resolve_reference_ids(
            conn, "education_specializations", "specialization_id", "specialization_name",
            {spec for c in deduped for d in c["qualification_degrees"] for spec in d["specializations"]},
        )
        # Each (degree, specialization) pairing gets its own id; both tables
        # store an array of those, one row per posting, mirroring posting_skills.
        degree_spec_to_id = _resolve_degree_specialization_ids(
            conn,
            {
                (degree_to_id[(d["degree"], d["level"])], specialization_to_id[spec])
                for c in deduped for d in c["qualification_degrees"] for spec in d["specializations"]
            },
        )

        # Keyed by fingerprint rather than job_id, since job_id isn't
        # assigned until the UPSERT below runs. Reused again in the
        # per-posting loop further down instead of recomputing.
        fingerprint_to_degree_ids = {}
        fingerprint_to_spec_ids = {}
        fingerprint_to_skill_ids = {}
        fingerprint_to_preferred_skill_ids = {}
        fingerprint_to_skill_groups = {}
        for c in deduped:
            degrees = c["qualification_degrees"]
            degree_ids = sorted({degree_to_id[(d["degree"], d["level"])] for d in degrees})
            spec_ids = sorted({
                degree_spec_to_id[(degree_to_id[(d["degree"], d["level"])], specialization_to_id[spec])]
                for d in degrees for spec in d["specializations"]
            })
            fingerprint_to_degree_ids[c["posting"]["fingerprint"]] = degree_ids
            fingerprint_to_spec_ids[c["posting"]["fingerprint"]] = spec_ids
            # Groups arrive as skill NAMES; resolve them to ids first,
            # because they determine what skill_ids must NOT contain.
            groups = []
            for group in c.get("skill_choice_groups", []):
                ids = sorted({skill_name_to_id[s] for s in group if s in skill_name_to_id})
                if len(ids) >= 2 and ids not in groups:
                    groups.append(ids)
            grouped_ids = {i for g in groups for i in g}

            # skill_ids holds only outright requirements; alternatives live in
            # skill_groups and are NOT repeated here. The full set is
            # skill_ids || skill_group_ids(skill_groups).
            fingerprint_to_skill_ids[c["posting"]["fingerprint"]] = sorted(
                skill_name_to_id[s] for s in c["skills"]
                if skill_name_to_id[s] not in grouped_ids)
            fingerprint_to_preferred_skill_ids[c["posting"]["fingerprint"]] = sorted(
                skill_name_to_id[s] for s in c["preferred_skills"])
            fingerprint_to_skill_groups[c["posting"]["fingerprint"]] = Json(groups)

        rows = [
            (
                c["posting"]["fingerprint"], c["posting"]["url"], c["posting"]["title"],
                c["posting"]["company"], c["posting"]["description"],
                c["posting"]["experience_min"], c["posting"]["experience_max"],
                c["posting"]["salary_min"], c["posting"]["salary_max"],
                c["posting"]["city_ids"], c["posting"]["unmapped_locations"],
                c["posting"]["working_type"], c["posting"]["is_full_time"],
                c["posting"]["contract_type"], c["posting"]["role_family"], c["posting"]["seniority_level"],
                role_category_to_id.get(c["posting"]["role_category"]), c["posting"]["naukri_role"],
                industry_type_to_id.get(c["posting"]["industry_type"]), department_to_id.get(c["posting"]["department"]),
                c["posting"]["posted_date"],
                c["posting"]["posted_raw"], c["posting"]["openings"],
                c["posting"]["applicant_count"], c["posting"]["applicant_count_qualifier"],
                c["posting"]["company_rating"], c["posting"]["company_reviews"], c["posting"]["company_badges"],
                c["posting"]["source_search"],
                c["posting"]["certifications"],
                fingerprint_to_degree_ids[c["posting"]["fingerprint"]],
                fingerprint_to_spec_ids[c["posting"]["fingerprint"]],
                fingerprint_to_skill_ids[c["posting"]["fingerprint"]],
                fingerprint_to_preferred_skill_ids[c["posting"]["fingerprint"]],
                fingerprint_to_skill_groups[c["posting"]["fingerprint"]],
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
            degree_rows, specialization_link_rows = [], []

            for c in deduped:
                job_id = fingerprint_to_job_id[c["posting"]["fingerprint"]]
                job_ids_touched.append(job_id)
                # One row per posting, even with no skills — unlike
                # qualifications below, which is one row per entry.
                # preferred_skill_ids is a subset, guaranteed here and CHECKed.
                fingerprint = c["posting"]["fingerprint"]
                skill_rows.append((job_id,
                                   fingerprint_to_skill_ids[fingerprint],
                                   fingerprint_to_preferred_skill_ids[fingerprint],
                                   fingerprint_to_skill_groups[fingerprint]))
                qualification_rows += [(job_id, q["level"], q["field_of_study"]) for q in c["qualifications"]]
                city_rows += [(job_id, city_id) for city_id in c["posting"]["city_ids"]]

                if fingerprint_to_degree_ids[fingerprint]:
                    degree_rows.append((job_id, fingerprint_to_degree_ids[fingerprint]))
                if fingerprint_to_spec_ids[fingerprint]:
                    specialization_link_rows.append((job_id, fingerprint_to_spec_ids[fingerprint]))

            # Rebuild each touched posting's skills/qualifications/cities/
            # degrees — cheap at this scale, and avoids stale rows when a
            # posting's skills or location change between scrapes.
            if job_ids_touched:
                cur.execute("DELETE FROM posting_skills WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_qualifications WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_cities WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_qualification_specializations WHERE job_id = ANY(%s)", (job_ids_touched,))
                cur.execute("DELETE FROM posting_qualification_degrees WHERE job_id = ANY(%s)", (job_ids_touched,))

            if skill_rows:
                execute_values(cur, "INSERT INTO posting_skills (job_id, skill_ids, preferred_skill_ids, skill_groups) VALUES %s", skill_rows)
            if qualification_rows:
                execute_values(cur, "INSERT INTO posting_qualifications (job_id, level, field_of_study) VALUES %s", qualification_rows)
            if city_rows:
                execute_values(cur, "INSERT INTO posting_cities (job_id, city_id) VALUES %s ON CONFLICT DO NOTHING", city_rows)
            if degree_rows:
                execute_values(cur, "INSERT INTO posting_qualification_degrees (job_id, accepted_degree_ids) VALUES %s", degree_rows)
            if specialization_link_rows:
                execute_values(cur, "INSERT INTO posting_qualification_specializations (job_id, accepted_degree_specialization_ids) VALUES %s", specialization_link_rows)

        conn.commit()
        return new_count, repeat_count
    finally:
        conn.close()


# =====================================================================
# SCRAPE HEALTH — surfaces a broken selector in the run's own log, rather than
# as a gap someone notices by hand months later (which is how the last two
# extraction bugs were actually found).
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


def snapshot_daily_skills() -> int:
    """Records today's skill counts by calling the SQL function of the
    same name (defined in trends_setup.sql). Returns the row count it
    wrote.

    Called at the end of every scrape rather than only from
    jobmarket.bat, because a scrape started any other way (a
    manual run, a re-run of one search) would otherwise never snapshot
    — and a day that isn't snapshotted is gone permanently, since
    cleaned_postings only ever shows the present. The SQL function
    recalculates rather than duplicating on a same-day re-run, so
    calling it once per search URL is harmless.

    This is exactly how three days were silently lost: the function
    still referenced posting_skills.skill after that table moved to
    skill_ids INT[], so it raised on every run, into a log nobody
    reads. Failures here are surfaced loudly by the caller."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT snapshot_daily_skills()")
            written = cur.fetchone()[0]
        conn.commit()
        return written
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


def pending_locations() -> list[dict]:
    """Location fragments that resolved to no city, minus the ones that are not
    cities at all.

    Every other reference table auto-registers an unseen value; cities cannot,
    because cities.state is NOT NULL and a bare fragment gives nothing to fill
    it with. So the fragment lands in unmapped_locations instead — and this
    surfaces that backlog, since a column nobody reads is the same as dropping
    it. Anything listed here needs a CITY_ALIASES entry and a state.

    Excludes state names ("Telangana" is not a city) and non-place tokens
    ("pan india"), which should stay unmapped rather than be registered.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT u AS fragment, COUNT(*)::int AS postings
                FROM cleaned_postings, LATERAL unnest(unmapped_locations) u
                WHERE NOT EXISTS (SELECT 1 FROM states s
                                   WHERE lower(s.state_name) = lower(trim(u)))
                  AND lower(trim(u)) NOT IN ('pan india', 'india', 'remote',
                                             'anywhere', 'work from home')
                GROUP BY u ORDER BY 2 DESC, 1
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
