"""
Analytics — the aggregate views.

Most of these accept the same filters as /postings, so a client can ask
"top skills, but only for DevOps roles in Bengaluru" rather than being
stuck with global totals.
"""

from fastapi import APIRouter, HTTPException, Query

from ..database import fetch_all, fetch_one, fetch_value, WhereBuilder
from ..models import (
    Summary, Bucket, SkillPair, SkillSuggestion, NamedCount,
    ScrapeHealthReport, ScrapeRunSummary, FieldHealthWarning, PendingLocation,
    SkillChoice, SkillFlexibility, ExperienceFlexibility, ClosureRate,
)
from job_database import check_field_health, pending_locations

router = APIRouter(prefix="/analytics", tags=["analytics"])


def scope(role_family, city, state, experience_min, experience_max,
          posted_after, company) -> WhereBuilder:
    """A smaller filter set than /postings, covering the dimensions that
    make sense to slice an aggregate by."""
    w = WhereBuilder()
    if role_family:
        w.add("c.role_family = ANY(%s)", list(role_family))
    if company:
        w.add("c.company ILIKE %s", f"%{company}%")
    if city:
        w.add_raw("""EXISTS (SELECT 1 FROM posting_cities pc
                     JOIN cities ci ON ci.city_id = pc.city_id
                     WHERE pc.job_id = c.job_id AND ci.city_name = ANY(%s))""")
        w.params.append(list(city))
    if state:
        w.add_raw("""EXISTS (SELECT 1 FROM posting_cities pc
                     JOIN cities ci ON ci.city_id = pc.city_id
                     WHERE pc.job_id = c.job_id AND ci.state = ANY(%s))""")
        w.params.append(list(state))
    w.add("c.experience_min >= %s", experience_min)
    w.add("c.experience_min <= %s", experience_max)
    w.add("c.posted_date >= %s", posted_after)
    return w


@router.get("/summary", response_model=Summary, summary="Headline figures")
def summary():
    row = fetch_one("""
        SELECT
            COUNT(*)::int                                          AS total_postings,
            COUNT(*) FILTER (WHERE last_seen_date >= CURRENT_DATE - 7)::int
                                                                   AS active_last_7_days,
            COUNT(*) FILTER (WHERE first_seen_date >= CURRENT_DATE - 7)::int
                                                                   AS new_postings_7d,
            COUNT(DISTINCT company)::int                           AS companies,
            COUNT(DISTINCT role_family)::int                       AS distinct_roles,
            COUNT(*) FILTER (WHERE salary_min IS NOT NULL)::int     AS postings_with_salary,
            MIN(posted_date)                                       AS earliest_posting,
            MAX(posted_date)                                       AS latest_posting,
            MAX(openings)::int                                     AS max_openings
        FROM cleaned_postings
    """) or {}

    row["distinct_skills"] = fetch_value(
        "SELECT COUNT(DISTINCT s) FROM posting_skills, unnest(skill_ids) AS s") or 0
    row["cities_covered"] = fetch_value(
        "SELECT COUNT(DISTINCT city_id) FROM posting_cities") or 0
    row["postings_with_education"] = fetch_value(
        "SELECT COUNT(DISTINCT job_id) FROM posting_qualifications") or 0
    row["median_openings"] = fetch_value("""
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY openings)
        FROM cleaned_postings WHERE openings IS NOT NULL""")
    # Divided by ALL postings, not just those with skills, so a posting with
    # none still counts in the denominator. COALESCE: array_length('{}') is NULL.
    row["avg_skills_per_posting"] = fetch_value("""
        SELECT ROUND(COALESCE(SUM(array_length(skill_ids, 1)), 0)::numeric / NULLIF(%s, 0), 2)::float
        FROM posting_skills
    """, (row.get("total_postings") or 0,)) or 0.0

    total = row.get("total_postings") or 1
    row["salary_disclosure_pct"] = round(
        100.0 * (row.get("postings_with_salary") or 0) / total, 2)

    return Summary(**row)


@router.get("/skills", response_model=list[NamedCount], summary="Skill demand")
def skill_demand(
    limit: int = Query(25, ge=1, le=200),
    include_blocked: bool = Query(False),
    role_family: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
    experience_min: int | None = Query(None, ge=0),
    experience_max: int | None = Query(None, ge=0),
    posted_after: str | None = Query(None),
    company: str | None = Query(None),
):
    w = scope(role_family, city, state, experience_min, experience_max,
              posted_after, company)
    if not include_blocked:
        w.add_raw("sk.skill_name NOT IN (SELECT skill FROM skill_blocklist)")

    return fetch_all(f"""
        SELECT sk.skill_name AS name, COUNT(*)::int AS postings
        FROM cleaned_postings c
        JOIN posting_skills ps ON ps.job_id = c.job_id
        JOIN LATERAL unnest(ps.skill_ids) AS u(skill_id) ON true
        JOIN skills sk ON sk.skill_id = u.skill_id
        {w.sql}
        GROUP BY sk.skill_name ORDER BY postings DESC LIMIT %s
    """, w.values + (limit,))


@router.get("/roles", response_model=list[Bucket], summary="Role distribution")
def role_distribution(
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
    experience_min: int | None = Query(None, ge=0),
    experience_max: int | None = Query(None, ge=0),
):
    w = scope(None, city, state, experience_min, experience_max, None, None)
    total = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values) or 1

    return fetch_all(f"""
        SELECT COALESCE(c.role_family, 'Other') AS bucket,
               COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM cleaned_postings c {w.sql}
        GROUP BY bucket ORDER BY postings DESC
    """, w.values)


@router.get("/seniority", response_model=list[Bucket], summary="Seniority mix, inferred from title")
def seniority_distribution(
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
):
    # Denominator is postings whose title carried a seniority word, not all
    # postings — most carry none. Same reasoning as qualification_distribution().
    w = scope(None, city, state, None, None, None, None)
    w.add_raw("c.seniority_level IS NOT NULL")
    total = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values) or 1

    return fetch_all(f"""
        SELECT c.seniority_level AS bucket,
               COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM cleaned_postings c {w.sql}
        GROUP BY bucket ORDER BY postings DESC
    """, w.values)


@router.get("/experience", response_model=list[Bucket], summary="Experience bands")
def experience_distribution(
    role_family: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
):
    w = scope(role_family, city, state, None, None, None, None)
    total = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values) or 1

    return fetch_all(f"""
        SELECT
            CASE
                WHEN c.experience_min IS NULL THEN 'Not stated'
                WHEN c.experience_min <= 1  THEN '0-1 years'
                WHEN c.experience_min <= 3  THEN '2-3 years'
                WHEN c.experience_min <= 6  THEN '4-6 years'
                WHEN c.experience_min <= 10 THEN '7-10 years'
                ELSE '10+ years'
            END AS bucket,
            COUNT(*)::int AS postings,
            ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM cleaned_postings c {w.sql}
        GROUP BY bucket ORDER BY postings DESC
    """, w.values)


@router.get("/locations", response_model=list[Bucket], summary="Geographic spread")
def location_distribution(
    by: str = Query("city", pattern="^(city|state)$"),
    role_family: list[str] | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
):
    w = WhereBuilder()
    if role_family:
        w.add("c.role_family = ANY(%s)", list(role_family))

    total = fetch_value(
        f"SELECT COUNT(*) FROM posting_cities pc "
        f"JOIN cleaned_postings c ON c.job_id = pc.job_id {w.sql}", w.values) or 1

    column = "ci.city_name" if by == "city" else "ci.state"
    return fetch_all(f"""
        SELECT {column} AS bucket, COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM posting_cities pc
        JOIN cities ci ON ci.city_id = pc.city_id
        JOIN cleaned_postings c ON c.job_id = pc.job_id
        {w.sql}
        GROUP BY {column} ORDER BY postings DESC LIMIT %s
    """, w.values + (limit,))


@router.get("/qualifications", response_model=list[Bucket], summary="Education level breakdown")
def qualification_distribution():
    # Denominator is postings disclosing any education requirement, not all
    # postings — the field is newer than some rows.
    total = fetch_value("SELECT COUNT(DISTINCT job_id) FROM posting_qualifications") or 1
    return fetch_all(f"""
        SELECT level AS bucket, COUNT(DISTINCT job_id)::int AS postings,
               ROUND(100.0 * COUNT(DISTINCT job_id) / {total}, 2)::float AS share_pct
        FROM posting_qualifications
        GROUP BY level ORDER BY postings DESC
    """)


@router.get("/skill-categories", response_model=list[Bucket], summary="Skill category mix")
def skill_category_mix(
    role_family: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
    experience_min: int | None = Query(None, ge=0),
    experience_max: int | None = Query(None, ge=0),
):
    # Counts skill MENTIONS, not postings, so shares sum to 100% and read as a
    # composition. Uncategorized skills are excluded rather than bucketed as
    # "Other" — most are generic Naukri tags and would dilute the signal.
    w = scope(role_family, city, state, experience_min, experience_max, None, None)
    w.add_raw("sk.category IS NOT NULL")

    total = fetch_value(f"""
        SELECT COUNT(*)
        FROM cleaned_postings c
        JOIN posting_skills ps ON ps.job_id = c.job_id
        JOIN LATERAL unnest(ps.skill_ids) AS u(skill_id) ON true
        JOIN skills sk ON sk.skill_id = u.skill_id
        {w.sql}
    """, w.values) or 1

    return fetch_all(f"""
        SELECT sk.category AS bucket, COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM cleaned_postings c
        JOIN posting_skills ps ON ps.job_id = c.job_id
        JOIN LATERAL unnest(ps.skill_ids) AS u(skill_id) ON true
        JOIN skills sk ON sk.skill_id = u.skill_id
        {w.sql}
        GROUP BY sk.category ORDER BY postings DESC
    """, w.values)


@router.get("/openings", response_model=list[Bucket], summary="Vacancies per posting")
def openings_distribution():
    total = fetch_value(
        "SELECT COUNT(*) FROM cleaned_postings WHERE openings IS NOT NULL") or 1
    return fetch_all(f"""
        SELECT openings::text AS bucket, COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM cleaned_postings
        WHERE openings IS NOT NULL
        GROUP BY openings ORDER BY openings
    """)


@router.get("/co-occurrence", response_model=list[SkillPair],
            summary="Which skills are requested together")
def co_occurrence(
    top_skills: int = Query(20, ge=2, le=50,
        description="Restrict to the N most-requested skills before pairing"),
    limit: int = Query(100, ge=1, le=1000),
    min_together: int = Query(1, ge=1),
):
    # One row per posting means pairing needs two unnests, one per side of the
    # self-join. `a.skill_id < b.skill_id` gives each pair once, never itself.
    return fetch_all("""
        WITH exploded AS (
            SELECT ps.job_id, u.skill_id
            FROM posting_skills ps, unnest(ps.skill_ids) AS u(skill_id)
        ),
        ranked AS (
            SELECT e.skill_id, COUNT(*) AS n
            FROM exploded e
            JOIN skills sk ON sk.skill_id = e.skill_id
            WHERE sk.skill_name NOT IN (SELECT skill FROM skill_blocklist)
            GROUP BY e.skill_id ORDER BY n DESC LIMIT %s
        )
        SELECT sa.skill_name AS skill_a, sb.skill_name AS skill_b,
               COUNT(*)::int AS together,
               ROUND(100.0 * COUNT(*) /
                     (SELECT n FROM ranked WHERE skill_id = a.skill_id), 2)::float
                   AS pct_of_skill_a
        FROM exploded a
        JOIN exploded b ON a.job_id = b.job_id AND a.skill_id < b.skill_id
        JOIN skills sa ON sa.skill_id = a.skill_id
        JOIN skills sb ON sb.skill_id = b.skill_id
        WHERE a.skill_id IN (SELECT skill_id FROM ranked)
          AND b.skill_id IN (SELECT skill_id FROM ranked)
        GROUP BY a.skill_id, b.skill_id, sa.skill_name, sb.skill_name
        HAVING COUNT(*) >= %s
        ORDER BY together DESC LIMIT %s
    """, (top_skills, min_together, limit))


@router.get("/skill-suggestions", response_model=list[SkillSuggestion],
            summary="What's asked for alongside a given set of skills")
def skill_suggestions(
    skill: list[str] = Query(..., description="Skills you already have"),
    limit: int = Query(15, ge=1, le=100),
    role_family: list[str] | None = Query(None),
):
    w = WhereBuilder()
    w.add_raw("""EXISTS (
        SELECT 1 FROM posting_skills ps
        WHERE ps.job_id = c.job_id
          AND ps.skill_ids && (SELECT array_agg(skill_id) FROM skills WHERE skill_name = ANY(%s)))""")
    w.params.append(list(skill))
    if role_family:
        w.add("c.role_family = ANY(%s)", list(role_family))

    base = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values) or 0
    if base == 0:
        return []

    return fetch_all(f"""
        SELECT sk.skill_name AS skill, COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {base}, 2)::float AS share_pct
        FROM cleaned_postings c
        JOIN posting_skills ps ON ps.job_id = c.job_id
        JOIN LATERAL unnest(ps.skill_ids) AS u(skill_id) ON true
        JOIN skills sk ON sk.skill_id = u.skill_id
        {w.sql}
          AND NOT (sk.skill_name = ANY(%s))
          AND sk.skill_name NOT IN (SELECT skill FROM skill_blocklist)
        GROUP BY sk.skill_name ORDER BY postings DESC LIMIT %s
    """, w.values + (list(skill), limit))


@router.get("/scrape-health", response_model=ScrapeHealthReport, summary="Scraper pipeline health")
def scrape_health(lookback: int = Query(20, ge=1, le=100)):
    # Uses job_database.py's own connection, not the pool — a deliberate bend.
    # The logic already lives there for the in-run warning; duplicating it here
    # would let the two drift apart.
    runs = fetch_all("""
        SELECT run_id, search_url, started_at, finished_at, postings_found,
               postings_scraped, postings_written, storage_ok, error_message,
               EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_seconds
        FROM scrape_runs
        ORDER BY started_at DESC LIMIT %s
    """, (lookback,))

    if not runs:
        return ScrapeHealthReport()

    warnings = check_field_health(runs[0]["run_id"])
    return ScrapeHealthReport(
        latest_run=ScrapeRunSummary(**runs[0]),
        warnings=[FieldHealthWarning(**w) for w in warnings],
        recent_runs=[ScrapeRunSummary(**r) for r in runs],
        pending_locations=[PendingLocation(**p) for p in pending_locations()],
    )


# ---------------------------------------------------------------------
# Skill choices — the only endpoints that separate "you must know X" from
# "X, or something like it, is fine".
# ---------------------------------------------------------------------
@router.get("/skill-choices", response_model=list[SkillChoice],
            summary="Skill sets employers treat as interchangeable")
def skill_choices(
    limit: int = Query(20, ge=1, le=200),
    min_postings: int = Query(2, ge=1,
        description="Drop one-off sets; raise for only well-established swaps"),
):
    # Names sorted inside each set so the same choice written in a different id
    # order still counts together. A join, not a correlated subquery per group:
    # that form cost 40 ms against 2.8 ms here, for identical output.
    return fetch_all("""
        WITH members AS (
            SELECT c.job_id, g.ord, e.id::int AS skill_id
            FROM cleaned_postings c,
                 LATERAL jsonb_array_elements(c.skill_groups) WITH ORDINALITY g(grp, ord),
                 LATERAL jsonb_array_elements_text(g.grp) e(id)
        ),
        sets AS (
            SELECT m.job_id, m.ord,
                   array_agg(sk.skill_name ORDER BY sk.skill_name) AS names
            FROM members m
            JOIN skills sk ON sk.skill_id = m.skill_id
            GROUP BY m.job_id, m.ord
        )
        SELECT names AS skills, COUNT(*)::int AS postings
        FROM sets
        GROUP BY names
        HAVING COUNT(*) >= %s
        ORDER BY postings DESC, names
        LIMIT %s
    """, (min_postings, limit))


@router.get("/skill-flexibility", response_model=list[SkillFlexibility],
            summary="How negotiable each skill is")
def skill_flexibility(
    limit: int = Query(25, ge=1, le=200),
    min_postings: int = Query(5, ge=1),
):
    """Splits each skill's demand into 'asked for outright' versus
    'would accept a substitute'. Only skills that appear in at least one
    choice group can score above zero, so the list is naturally ranked
    toward tools with real competitors."""
    return fetch_all("""
        WITH per_skill AS (
            SELECT sk.skill_id, sk.skill_name,
                   COUNT(*) FILTER (WHERE sk.skill_id = ANY(c.skill_ids))                     AS required,
                   COUNT(*) FILTER (WHERE sk.skill_id = ANY(skill_group_ids(c.skill_groups))) AS alternative
            FROM cleaned_postings c
            JOIN skills sk ON sk.skill_id = ANY(c.skill_ids || skill_group_ids(c.skill_groups))
            WHERE sk.skill_name NOT IN (SELECT skill FROM skill_blocklist)
            GROUP BY sk.skill_id, sk.skill_name
        ),
        -- What each skill is actually swapped with: every other member
        -- of a group it appears in, most frequently paired first.
        swaps AS (
            SELECT a.skill_id,
                   array_agg(b.skill_name ORDER BY b.n DESC, b.skill_name) AS swaps
            FROM (SELECT DISTINCT sk.skill_id FROM skills sk) a
            JOIN LATERAL (
                SELECT other.skill_name, COUNT(*) AS n
                FROM cleaned_postings c, LATERAL jsonb_array_elements(c.skill_groups) grp
                JOIN skills other ON other.skill_id IN (SELECT jsonb_array_elements_text(grp)::int)
                WHERE a.skill_id IN (SELECT jsonb_array_elements_text(grp)::int)
                  AND other.skill_id <> a.skill_id
                GROUP BY other.skill_name
            ) b ON true
            GROUP BY a.skill_id
        )
        SELECT p.skill_name AS skill,
               p.required::int, p.alternative::int,
               (p.required + p.alternative)::int AS total,
               ROUND(100.0 * p.alternative / NULLIF(p.required + p.alternative, 0), 1)::float AS negotiable_pct,
               COALESCE(s.swaps, '{}') AS swaps
        FROM per_skill p
        LEFT JOIN swaps s ON s.skill_id = p.skill_id
        WHERE (p.required + p.alternative) >= %s AND p.alternative > 0
        ORDER BY negotiable_pct DESC, total DESC
        LIMIT %s
    """, (min_postings, limit))


@router.get("/flexibility-by-experience", response_model=list[ExperienceFlexibility],
            summary="Willingness to accept a substitute, by experience band")
def flexibility_by_experience(
    role_family: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
):
    """Shows how often postings in each experience band offer an either/or
    skill instead of naming one outright. Bands match /analytics/experience so
    the two charts read against the same axis."""
    w = scope(role_family, city, state, None, None, None, None)
    # experience_min covers 97% of postings, so unlike the seniority split this
    # is a breakdown of nearly everything rather than of a self-selected few.
    return fetch_all(f"""
        SELECT
            CASE
                WHEN c.experience_min IS NULL THEN 'Not stated'
                WHEN c.experience_min <= 1  THEN '0-1 years'
                WHEN c.experience_min <= 3  THEN '2-3 years'
                WHEN c.experience_min <= 6  THEN '4-6 years'
                WHEN c.experience_min <= 10 THEN '7-10 years'
                ELSE '10+ years'
            END AS bucket,
            COUNT(*)::int AS postings,
            COUNT(*) FILTER (WHERE c.skill_groups <> '[]')::int AS offering_a_choice,
            ROUND(100.0 * COUNT(*) FILTER (WHERE c.skill_groups <> '[]')
                  / COUNT(*), 1)::float AS pct_offering_a_choice
        FROM cleaned_postings c {w.sql}
        GROUP BY bucket
        HAVING COUNT(*) >= 5
        ORDER BY pct_offering_a_choice DESC
    """, w.values)


# ---------------------------------------------------------------------
# CLOSURES
# ---------------------------------------------------------------------
# Only the two dimensions that survive scrutiny. Both come from fields the
# scraper has captured since its first run, so neither carries the cohort
# confound that makes department/industry/education unusable: those landed
# on 19 Aug, so their "not stated" bucket is really "collected earlier",
# and earlier postings have had longer to close.
CLOSURE_DIMENSIONS = {
    "experience_band": """
        CASE
            WHEN c.experience_min IS NULL THEN 'Not stated'
            WHEN c.experience_min <= 1  THEN '0-1 years'
            WHEN c.experience_min <= 3  THEN '2-3 years'
            WHEN c.experience_min <= 6  THEN '4-6 years'
            WHEN c.experience_min <= 10 THEN '7-10 years'
            ELSE '10+ years'
        END
    """,
    "role_family": "COALESCE(c.role_family, 'Uncategorised')",
}


@router.get("/closures", response_model=list[ClosureRate],
            summary="How fast postings close, by group")
def closures(
    dimension: str = Query("experience_band",
                           description="experience_band or role_family"),
    min_postings: int = Query(15, ge=1,
                              description="Groups smaller than this are omitted"),
    role_family: list[str] | None = Query(None),
    city: list[str] | None = Query(None),
    state: list[str] | None = Query(None),
):
    """Closure rate per group, adjusted for how long each posting has been
    watched.

    Rank on `per_100_posting_days`. `pct_closed` is the intuitive number and
    the misleading one: it compares groups that have been observed for
    different lengths of time.

    Company and location are deliberately not offered. Company splits 522
    postings across 239 employers, and location is 520 Hyderabad because that
    is what the searches target -- neither can separate groups.
    """
    if dimension not in CLOSURE_DIMENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"dimension must be one of {sorted(CLOSURE_DIMENSIONS)}",
        )
    bucket_sql = CLOSURE_DIMENSIONS[dimension]
    w = scope(role_family, city, state, None, None, None, None)

    # is_expired IS NULL means never checked, which is neither open nor
    # closed -- counting it either way would be inventing an observation.
    return fetch_all(f"""
        SELECT
            {bucket_sql} AS bucket,
            COUNT(*)::int AS postings,
            COUNT(*) FILTER (WHERE c.is_expired)::int AS closed,
            ROUND(100.0 * COUNT(*) FILTER (WHERE c.is_expired)
                  / COUNT(*), 1)::float AS pct_closed,
            ROUND(AVG(CURRENT_DATE - c.first_seen_date), 1)::float
                AS mean_exposure_days,
            ROUND(100.0 * COUNT(*) FILTER (WHERE c.is_expired)
                  / NULLIF(SUM(CURRENT_DATE - c.first_seen_date), 0), 2)::float
                AS per_100_posting_days
        FROM cleaned_postings c
        {w.sql}{" AND" if w.sql else "WHERE"} c.is_expired IS NOT NULL
        GROUP BY bucket
        HAVING COUNT(*) >= {int(min_postings)}
        ORDER BY per_100_posting_days DESC NULLS LAST
    """, w.values)
