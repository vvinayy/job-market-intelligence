"""
Reference data — the canonical lists.

A client building filter controls needs to know what values are valid.
Hardcoding city names or role families in a frontend means they drift
out of sync with the database; these endpoints keep one source of truth.
"""

from fastapi import APIRouter, Query

from ..database import fetch_all, fetch_value
from ..models import City, State, NamedCount, DegreeCount, SkillInfo

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/cities", response_model=list[City], summary="Canonical cities")
def list_cities(with_postings_only: bool = Query(False,
                description="Exclude cities that have no postings yet")):
    having = "HAVING COUNT(pc.job_id) > 0" if with_postings_only else ""
    return fetch_all(f"""
        SELECT c.city_id, c.city_name, c.state,
               COUNT(pc.job_id)::int AS postings
        FROM cities c
        LEFT JOIN posting_cities pc ON pc.city_id = c.city_id
        GROUP BY c.city_id, c.city_name, c.state
        {having}
        ORDER BY postings DESC, c.city_name
    """)


@router.get("/states", response_model=list[State], summary="States with coverage")
def list_states():
    return fetch_all("""
        SELECT s.state_name,
               COUNT(DISTINCT c.city_id)::int AS cities,
               COUNT(pc.job_id)::int          AS postings
        FROM states s
        LEFT JOIN cities c ON c.state = s.state_name
        LEFT JOIN posting_cities pc ON pc.city_id = c.city_id
        GROUP BY s.state_name
        ORDER BY postings DESC, s.state_name
    """)


@router.get("/roles", response_model=list[NamedCount], summary="Role families")
def list_roles():
    return fetch_all("""
        SELECT COALESCE(role_family, 'Other') AS name, COUNT(*)::int AS postings
        FROM cleaned_postings
        GROUP BY name ORDER BY postings DESC
    """)


@router.get("/role-categories", response_model=list[NamedCount], summary="Naukri's Role Category tag")
def list_role_categories():
    return fetch_all("""
        SELECT rc.name, COUNT(c.job_id)::int AS postings
        FROM role_categories rc
        LEFT JOIN cleaned_postings c ON c.role_category_id = rc.role_category_id
        GROUP BY rc.name ORDER BY postings DESC, rc.name
    """)


@router.get("/departments", response_model=list[NamedCount], summary="Department")
def list_departments():
    return fetch_all("""
        SELECT d.name, COUNT(c.job_id)::int AS postings
        FROM departments d
        LEFT JOIN cleaned_postings c ON c.department_id = d.department_id
        GROUP BY d.name ORDER BY postings DESC, d.name
    """)


@router.get("/industry-types", response_model=list[NamedCount], summary="Industry Type")
def list_industry_types():
    return fetch_all("""
        SELECT it.name, COUNT(c.job_id)::int AS postings
        FROM industry_types it
        LEFT JOIN cleaned_postings c ON c.industry_type_id = it.industry_type_id
        GROUP BY it.name ORDER BY postings DESC, it.name
    """)


@router.get("/education-degrees", response_model=list[DegreeCount], summary="Accepted degrees, broken out individually")
def list_education_degrees():
    # posting_qualification_degrees is one row per posting with an array
    # of degree_ids (mirrors posting_skills) -- unnest to count postings
    # per degree instead of joining on a column that no longer exists.
    return fetch_all("""
        SELECT ed.degree_name AS name, ed.level, COUNT(pqd.job_id)::int AS postings
        FROM education_degrees ed
        LEFT JOIN posting_qualification_degrees pqd ON ed.degree_id = ANY(pqd.degree_ids)
        GROUP BY ed.degree_name, ed.level ORDER BY postings DESC, ed.degree_name
    """)


@router.get("/education-specializations", response_model=list[NamedCount], summary="Accepted specializations")
def list_education_specializations():
    # DISTINCT job_id -- a posting reaching the same specialization
    # through two different degrees (e.g. "B.Tech in CSE" and "MCA in
    # CSE") must still only count once, not once per degree pairing.
    return fetch_all("""
        SELECT es.specialization_name AS name, COUNT(DISTINCT pqs.job_id)::int AS postings
        FROM education_specializations es
        LEFT JOIN education_degree_specializations eds ON eds.specialization_id = es.specialization_id
        LEFT JOIN posting_qualification_specializations pqs
            ON eds.degree_specialization_id = ANY(pqs.degree_specialization_ids)
        GROUP BY es.specialization_name ORDER BY postings DESC, es.specialization_name
    """)


@router.get("/companies", response_model=list[NamedCount], summary="Companies")
def list_companies(
    limit: int = Query(100, ge=1, le=1000),
    min_postings: int = Query(1, ge=1),
    search: str | None = Query(None, description="Partial name match"),
):
    params: list = [min_postings]
    name_filter = ""
    if search:
        name_filter = "AND company ILIKE %s"
        params.insert(0, f"%{search}%")

    params.append(limit)
    return fetch_all(f"""
        SELECT company AS name, COUNT(*)::int AS postings
        FROM cleaned_postings
        WHERE company IS NOT NULL {name_filter}
        GROUP BY company
        HAVING COUNT(*) >= %s
        ORDER BY postings DESC, company
        LIMIT %s
    """, tuple(params))


@router.get("/skills", response_model=list[SkillInfo], summary="Known skills")
def list_skills(
    limit: int = Query(200, ge=1, le=2000),
    min_postings: int = Query(1, ge=1,
        description="Filters out one-off tags; raise this to get a stable vocabulary"),
    include_blocked: bool = Query(False,
        description="Include generic terms like 'Agile' that are normally excluded"),
    search: str | None = Query(None),
):
    total = fetch_value("SELECT COUNT(*) FROM cleaned_postings") or 1

    clauses, params = [], []
    if not include_blocked:
        clauses.append("sk.skill_name NOT IN (SELECT skill FROM skill_blocklist)")
    if search:
        clauses.append("sk.skill_name ILIKE %s")
        params.append(f"%{search}%")

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([min_postings, limit])

    return fetch_all(f"""
        SELECT sk.skill_name AS skill,
               COUNT(*)::int AS postings,
               ROUND(100.0 * COUNT(*) / {total}, 2)::float AS share_pct
        FROM posting_skills ps
        JOIN LATERAL unnest(ps.skill_ids) AS u(skill_id) ON true
        JOIN skills sk ON sk.skill_id = u.skill_id
        {where}
        GROUP BY sk.skill_name
        HAVING COUNT(*) >= %s
        ORDER BY postings DESC, sk.skill_name
        LIMIT %s
    """, tuple(params))


@router.get("/working-types", response_model=list[NamedCount])
def list_working_types():
    return fetch_all("""
        SELECT COALESCE(working_type, 'Not stated') AS name, COUNT(*)::int AS postings
        FROM cleaned_postings GROUP BY name ORDER BY postings DESC
    """)


@router.get("/employment-types", response_model=list[NamedCount])
def list_employment_types():
    return fetch_all("""
        SELECT COALESCE(employment_type, 'Not stated') AS name, COUNT(*)::int AS postings
        FROM cleaned_postings GROUP BY name ORDER BY postings DESC
    """)


@router.get("/contract-types", response_model=list[NamedCount])
def list_contract_types():
    return fetch_all("""
        SELECT COALESCE(contract_type, 'Not stated') AS name, COUNT(*)::int AS postings
        FROM cleaned_postings GROUP BY name ORDER BY postings DESC
    """)
