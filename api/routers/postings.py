"""
Postings — the individual job records, with filtering.

This is the endpoint with the most parameters, because it's the one a
client uses to answer "show me the jobs matching X". Every filter is
optional; supplying none returns everything, paginated.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..database import fetch_all, fetch_one, fetch_value, WhereBuilder
from ..models import PostingSummary, PostingDetail, PostingPage, Qualification

# The one place the API reaches outside itself. responsibilities_text /
# requirements_text used to be stored columns, but they are a pure
# function of `description` — verified byte-identical on every row — so
# storing them cost ~24% of the table to hold nothing new. Recomputed
# here instead, which is cheap because this is the single-posting detail
# endpoint: one description, not a scan.
from cleaning import split_description_sections

router = APIRouter(prefix="/postings", tags=["postings"])


# Sorting is restricted to a known list rather than accepting arbitrary
# column names. A caller-supplied ORDER BY would be an injection route,
# since column names can't be parameterised the way values can.
SORTABLE = {
    "posted_date": "c.posted_date",
    "experience_min": "c.experience_min",
    "experience_max": "c.experience_max",
    "salary_min": "c.salary_min",
    "salary_max": "c.salary_max",
    "openings": "c.openings",
    "applicant_count": "c.applicant_count",
    "times_seen": "c.times_seen",
    "first_seen": "c.first_seen_date",
    "last_seen": "c.last_seen_date",
    "company": "c.company",
    "title": "c.title",
}

# Selected in both list and detail responses. The description is
# deliberately excluded here — it's large, and returning it for 50 rows
# would make every page response enormous. skills comes from
# posting_skills now, not an array column.
BASE_SELECT = """
    SELECT
        c.job_id, c.title, c.company, c.role_family, c.seniority_level,
        rc.name AS role_category, c.naukri_role, it.name AS industry_type, d.name AS department,
        c.experience_min, c.experience_max,
        c.salary_min, c.salary_max,
        COALESCE(
            (SELECT array_agg(sk.skill_name ORDER BY sk.skill_name)
             FROM posting_skills ps
             JOIN skills sk ON sk.skill_id = ANY(ps.skill_ids)
             WHERE ps.job_id = c.job_id),
            '{}'
        ) AS skills,
        COALESCE(
            (SELECT array_agg(ci.city_name ORDER BY ci.city_name)
             FROM posting_cities pc
             JOIN cities ci ON ci.city_id = pc.city_id
             WHERE pc.job_id = c.job_id),
            '{}'
        ) AS cities,
        c.working_type, c.employment_type, c.contract_type,
        c.posted_date, c.openings, c.applicant_count, c.applicant_count_qualifier,
        c.company_rating, c.company_reviews, c.url
    FROM cleaned_postings c
    LEFT JOIN role_categories rc ON rc.role_category_id = c.role_category_id
    LEFT JOIN departments d ON d.department_id = c.department_id
    LEFT JOIN industry_types it ON it.industry_type_id = c.industry_type_id
"""


def build_filters(
    skill, skills_all, role_family, seniority_level, company, city, state,
    experience_min, experience_max, has_salary, salary_min, salary_max,
    working_type, employment_type, contract_type, qualification_level,
    posted_after, posted_before, seen_after, search, min_openings,
) -> WhereBuilder:
    """Turn optional query parameters into a parameterised WHERE clause."""
    w = WhereBuilder()

    # "any of these" vs "all of these" — two different questions, so
    # both are offered, same as before skills moved out of an array.
    if skill:
        w.add_raw("""EXISTS (
            SELECT 1 FROM posting_skills ps
            WHERE ps.job_id = c.job_id
              AND ps.skill_ids && (SELECT array_agg(skill_id) FROM skills WHERE skill_name = ANY(%s)))""")
        w.params.append(list(skill))
    if skills_all:
        # The count check guards against a requested name that doesn't
        # match any skill at all — without it, array_agg would silently
        # drop that name from the containment check below and the filter
        # would quietly accept postings missing a skill that was asked
        # for, instead of correctly returning nothing.
        w.add_raw("""(
            SELECT COUNT(*) FROM skills WHERE skill_name = ANY(%s)) = %s
            AND EXISTS (
                SELECT 1 FROM posting_skills ps
                WHERE ps.job_id = c.job_id
                  AND ps.skill_ids @> (SELECT COALESCE(array_agg(skill_id), '{}') FROM skills WHERE skill_name = ANY(%s)))""")
        w.params.append(list(skills_all))
        w.params.append(len(set(skills_all)))
        w.params.append(list(skills_all))

    if role_family:
        w.add("c.role_family = ANY(%s)", list(role_family))
    if seniority_level:
        w.add("c.seniority_level = ANY(%s)", list(seniority_level))
    if company:
        w.add("c.company ILIKE %s", f"%{company}%")

    if city:
        w.add_raw("""EXISTS (
            SELECT 1 FROM posting_cities pc
            JOIN cities ci ON ci.city_id = pc.city_id
            WHERE pc.job_id = c.job_id AND ci.city_name = ANY(%s))""")
        w.params.append(list(city))

    if state:
        w.add_raw("""EXISTS (
            SELECT 1 FROM posting_cities pc
            JOIN cities ci ON ci.city_id = pc.city_id
            WHERE pc.job_id = c.job_id AND ci.state = ANY(%s))""")
        w.params.append(list(state))

    # experience_max here means "the role's minimum requirement is at
    # most N" — i.e. someone with N years qualifies. Filtering on the
    # posting's own max would exclude roles open to more experience.
    w.add("c.experience_min >= %s", experience_min)
    w.add("c.experience_min <= %s", experience_max)

    if has_salary is True:
        w.add_raw("c.salary_min IS NOT NULL")
    elif has_salary is False:
        w.add_raw("c.salary_min IS NULL")

    w.add("c.salary_max >= %s", salary_min)
    w.add("c.salary_min <= %s", salary_max)

    if working_type:
        w.add("c.working_type = ANY(%s)", list(working_type))
    if employment_type:
        w.add("c.employment_type = ANY(%s)", list(employment_type))
    if contract_type:
        w.add("c.contract_type = ANY(%s)", list(contract_type))

    if qualification_level:
        w.add_raw("""EXISTS (
            SELECT 1 FROM posting_qualifications pq
            WHERE pq.job_id = c.job_id AND pq.level = ANY(%s))""")
        w.params.append(list(qualification_level))

    w.add("c.posted_date >= %s", posted_after)
    w.add("c.posted_date <= %s", posted_before)
    w.add("c.last_seen_date >= %s", seen_after)
    w.add("c.openings >= %s", min_openings)

    if search:
        w.add("(c.title ILIKE %s OR c.company ILIKE %s)", f"%{search}%", f"%{search}%")

    return w


@router.get("", response_model=PostingPage, summary="Search postings")
def list_postings(
    # --- content filters ---
    skill: list[str] | None = Query(None, description="Match postings with ANY of these skills"),
    skills_all: list[str] | None = Query(None, description="Match postings with ALL of these skills"),
    role_family: list[str] | None = Query(None, description="e.g. 'Data Scientist', 'DevOps Engineer'"),
    seniority_level: list[str] | None = Query(None, description="Inferred from title: Intern/Trainee, Junior, Associate, Senior, Lead/Principal, Manager/Leadership"),
    company: str | None = Query(None, description="Partial, case-insensitive company match"),
    search: str | None = Query(None, description="Free text across title and company"),

    # --- location ---
    city: list[str] | None = Query(None, description="Canonical city names"),
    state: list[str] | None = Query(None),

    # --- experience ---
    experience_min: int | None = Query(None, ge=0, le=50,
        description="Only roles requiring at least this many years"),
    experience_max: int | None = Query(None, ge=0, le=50,
        description="Only roles whose minimum requirement is at most this — i.e. roles you'd qualify for"),

    # --- pay ---
    has_salary: bool | None = Query(None, description="Filter to postings that do or don't disclose pay"),
    salary_min: float | None = Query(None, ge=0, description="Lowest acceptable upper bound, in LPA"),
    salary_max: float | None = Query(None, ge=0, description="Highest acceptable lower bound, in LPA"),

    # --- arrangement ---
    working_type: list[str] | None = Query(None, description="Remote, Hybrid, On-site"),
    employment_type: list[str] | None = Query(None, description="Full Time, Part Time"),
    contract_type: list[str] | None = Query(None, description="Permanent, Contract, Internship"),
    qualification_level: list[str] | None = Query(None, description="e.g. 'UG', 'PG', 'Doctorate'"),

    # --- dates ---
    posted_after: date | None = Query(None),
    posted_before: date | None = Query(None),
    seen_after: date | None = Query(None, description="Still listed on or after this date"),

    # --- other ---
    min_openings: int | None = Query(None, ge=1),

    # --- sorting and paging ---
    sort_by: str = Query("posted_date", description=f"One of: {', '.join(SORTABLE)}"),
    order: Literal["asc", "desc"] = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    if sort_by not in SORTABLE:
        raise HTTPException(400, f"sort_by must be one of: {', '.join(SORTABLE)}")

    w = build_filters(
        skill, skills_all, role_family, seniority_level, company, city, state,
        experience_min, experience_max, has_salary, salary_min, salary_max,
        working_type, employment_type, contract_type, qualification_level,
        posted_after, posted_before, seen_after, search, min_openings,
    )

    total = fetch_value(
        f"SELECT COUNT(*) FROM cleaned_postings c {w.sql}", w.values
    ) or 0

    # NULLS LAST matters: Postgres sorts NULL as largest, so a DESC sort
    # on posted_date would otherwise lead with postings that have no date.
    sql = (
        f"{BASE_SELECT}{w.sql} "
        f"ORDER BY {SORTABLE[sort_by]} {order.upper()} NULLS LAST, c.job_id DESC "
        f"LIMIT %s OFFSET %s"
    )
    rows = fetch_all(sql, w.values + (page_size, (page - 1) * page_size))

    return PostingPage(
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),   # ceiling division
        items=[PostingSummary(**r) for r in rows],
    )


@router.get("/{job_id}", response_model=PostingDetail, summary="One posting in full")
def get_posting(job_id: int):
    row = fetch_one(f"""
        {BASE_SELECT}
        WHERE c.job_id = %s
    """, (job_id,))

    if not row:
        raise HTTPException(404, f"No posting with job_id {job_id}")

    extra = fetch_one("""
        SELECT c.description,
               COALESCE(c.certifications, '{}') AS certifications,
               COALESCE(c.company_badges, '{}') AS company_badges,
               c.source_search,
               COALESCE(
                   (SELECT array_agg(sk.skill_name ORDER BY sk.skill_name)
                    FROM posting_skills ps
                    JOIN skills sk ON sk.skill_id = ANY(ps.preferred_skill_ids)
                    WHERE ps.job_id = c.job_id),
                   '{}'
               ) AS preferred_skills,
               c.first_seen_date, c.last_seen_date, c.times_seen,
               (c.last_seen_date - c.first_seen_date) AS days_listed
        FROM cleaned_postings c
        WHERE c.job_id = %s
    """, (job_id,)) or {}

    qualifications = fetch_all("""
        SELECT level, field_of_study FROM posting_qualifications WHERE job_id = %s ORDER BY level
    """, (job_id,))

    # Same facts as `qualifications` above, but broken out to individual
    # degrees via the reference tables instead of Naukri's flattened
    # display string -- e.g. "B.Tech / B.E." becomes two separate
    # entries here, each with only the specializations that actually
    # pair with it. Two CTEs unnest this posting's accepted_degree_ids
    # and accepted_degree_specialization_ids arrays; education_degree_
    # specializations is the global dictionary of every (degree,
    # specialization) pairing ever seen, filtered down to just the ones
    # this posting actually has via the IN clause.
    qualification_degrees = fetch_all("""
        WITH pqd AS (
            SELECT unnest(accepted_degree_ids) AS degree_id
            FROM posting_qualification_degrees WHERE job_id = %s
        ),
        pqs AS (
            SELECT unnest(accepted_degree_specialization_ids) AS degree_specialization_id
            FROM posting_qualification_specializations WHERE job_id = %s
        )
        SELECT ed.degree_name AS degree, ed.level,
               COALESCE(
                   array_agg(es.specialization_name ORDER BY es.specialization_name)
                       FILTER (WHERE es.specialization_name IS NOT NULL),
                   '{}'
               ) AS specializations
        FROM pqd
        JOIN education_degrees ed ON ed.degree_id = pqd.degree_id
        LEFT JOIN education_degree_specializations eds ON eds.degree_id = ed.degree_id
            AND eds.degree_specialization_id IN (SELECT degree_specialization_id FROM pqs)
        LEFT JOIN education_specializations es ON es.specialization_id = eds.specialization_id
        GROUP BY ed.degree_id, ed.degree_name, ed.level
        ORDER BY ed.level, ed.degree_name
    """, (job_id, job_id))

    sections = split_description_sections(extra.get("description"))

    return PostingDetail(
        **{**row, **extra},
        responsibilities_text=sections["responsibilities"],
        requirements_text=sections["requirements"],
        qualifications=[Qualification(**q) for q in qualifications],
        qualification_degrees=qualification_degrees,
    )
