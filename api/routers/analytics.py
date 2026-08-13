"""
Analytics — the aggregate views.

Most of these accept the same filters as /postings, so a client can ask
"top skills, but only for DevOps roles in Bengaluru" rather than being
stuck with global totals.
"""

from fastapi import APIRouter, Query

from ..database import fetch_all, fetch_one, fetch_value, WhereBuilder
from ..models import Summary, Bucket, SkillPair, SkillSuggestion, NamedCount

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
            COUNT(DISTINCT company)::int                           AS companies,
            COUNT(DISTINCT role_family)::int                       AS distinct_roles,
            COUNT(*) FILTER (WHERE salary_min IS NOT NULL)::int     AS postings_with_salary,
            ROUND(AVG(COALESCE(array_length(skills, 1), 0)), 2)::float
                                                                   AS avg_skills_per_posting,
            MIN(posted_date)                                       AS earliest_posting,
            MAX(posted_date)                                       AS latest_posting,
            MAX(openings)::int                                     AS max_openings
        FROM cleaned_postings
    """) or {}

    row["distinct_skills"] = fetch_value(
        "SELECT COUNT(DISTINCT s) FROM cleaned_postings, unnest(skills) s") or 0
    row["cities_covered"] = fetch_value(
        "SELECT COUNT(DISTINCT city_id) FROM posting_cities") or 0
    row["median_openings"] = fetch_value("""
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY openings)
        FROM cleaned_postings WHERE openings IS NOT NULL""")

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
        w.add_raw("skill NOT IN (SELECT skill FROM skill_blocklist)")

    return fetch_all(f"""
        SELECT skill AS name, COUNT(*)::int AS postings
        FROM cleaned_postings c, unnest(c.skills) AS skill
        {w.sql}
        GROUP BY skill ORDER BY postings DESC LIMIT %s
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
    # a.skill < b.skill keeps one row per pair rather than both
    # directions, and excludes a skill paired with itself.
    return fetch_all("""
        WITH ranked AS (
            SELECT skill, COUNT(*) AS n
            FROM cleaned_postings, unnest(skills) AS skill
            WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
            GROUP BY skill ORDER BY n DESC LIMIT %s
        )
        SELECT a.skill AS skill_a, b.skill AS skill_b,
               COUNT(*)::int AS together,
               ROUND(100.0 * COUNT(*) /
                     (SELECT n FROM ranked WHERE skill = a.skill), 2)::float
                   AS pct_of_skill_a
        FROM cleaned_postings c,
             unnest(c.skills) AS a(skill),
             unnest(c.skills) AS b(skill)
        WHERE a.skill < b.skill
          AND a.skill IN (SELECT skill FROM ranked)
          AND b.skill IN (SELECT skill FROM ranked)
        GROUP BY a.skill, b.skill
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
    w.add("c.skills && %s::text[]", list(skill))
    if role_family:
        w.add("c.role_family = ANY(%s)", list(role_family))

    base = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values) or 0
    if base == 0:
        return []

    return fetch_all(f"""
        SELECT s AS skill, COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {base}, 2)::float AS share_pct
        FROM cleaned_postings c, unnest(c.skills) AS s
        {w.sql}
          AND NOT (s = ANY(%s))
          AND s NOT IN (SELECT skill FROM skill_blocklist)
        GROUP BY s ORDER BY postings DESC LIMIT %s
    """, w.values + (list(skill), limit))
