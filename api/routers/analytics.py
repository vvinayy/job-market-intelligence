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
    # Sum of each posting's array length, divided by total postings — not
    # postings-with-skills — so a posting with zero skills still belongs
    # in the average's denominator. COALESCE covers postings whose
    # skill_ids is '{}', where array_length returns NULL rather than 0.
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
    # Denominator is postings that disclose ANY education requirement,
    # not all postings — Education is a newer field, only populated for
    # postings scraped after it was added, so dividing by every posting
    # would understate these percentages for a reason that has nothing
    # to do with actual demand.
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
    # Counts skill MENTIONS, not postings — a posting with three
    # Cloud/DevOps skills contributes three, so shares add up to a real
    # composition ("this role's skill mix is 58% Cloud/DevOps") rather
    # than an overlap count that wouldn't sum to 100%. Only categorized
    # skills count towards the denominator: most of Naukri's own tags
    # (Agile, Communication Skills, generic role titles) aren't specific
    # enough to categorize, and folding them into a fake "Other" bucket
    # would dilute the real signal rather than add to it.
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
    # posting_skills is one row per posting now, so pairing requires
    # unnesting each posting's skill_ids twice (once per side of the
    # self-join) — the cost this design trades away for its smaller
    # footprint. a.skill_id < b.skill_id keeps one row per pair rather
    # than both directions, and excludes a skill paired with itself.
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
