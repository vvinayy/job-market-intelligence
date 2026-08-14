"""
Cleaning layer — raw_postings -> cleaned_postings, in Python.

Previously this transformation lived as PL/pgSQL functions in
cleaning_setup.sql, schema_v2.sql and roles_setup.sql: normalize_skill(),
parse_range_min/max(), parse_employment_type(), parse_contract_type(),
normalize_working_type(), classify_role(), and clean_and_populate()
itself. All of that logic now lives here instead — Postgres is read
from and written to, but no cleaning decision is made inside it anymore.

Same tables, same columns, same values. cleaned_postings and
posting_cities keep the exact shape schema_v2.sql defined; every
computed value here matches what the SQL functions were already
producing (verified row-for-row against the live database before this
replaced them — see the migration notes in the project history).

One deliberate behavior change from the old SQL: cleaned_postings.url/
title/company now refresh on every re-scrape, matching the fix already
applied to raw_postings. The old SQL never refreshed them here, which
had become inconsistent with that fix.

Run standalone:
    python cleaning.py
Or import:
    from cleaning import clean_and_populate
    clean_and_populate()
"""

import os
import re
import math

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor


def get_connection():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
    )


# =====================================================================
# SKILL NORMALIZATION — ported from skill_aliases.
# =====================================================================
SKILL_ALIASES = {
    "ai": "AI", "artificial intelligence": "AI",
    "api": "API",
    "amazon web services": "AWS", "aws": "AWS",
    "azure": "Azure", "microsoft azure": "Azure",
    "ci cd": "CI/CD", "ci/cd": "CI/CD",
    "css": "CSS",
    "devops": "DevOps",
    "elt": "ELT",
    "etl": "ETL",
    "gcp": "GCP", "google cloud": "GCP", "google cloud platform": "GCP",
    "github": "GitHub",
    "gitlab": "GitLab",
    "graphql": "GraphQL",
    "html": "HTML",
    "iot": "IoT",
    "javascript": "JavaScript", "js": "JavaScript",
    "apache kafka": "Kafka", "kafka": "Kafka",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "llm": "LLM",
    "machine learning": "Machine Learning", "ml": "Machine Learning",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "nlp": "NLP",
    "node": "Node.js", "node.js": "Node.js", "nodejs": "Node.js",
    "nosql": "NoSQL",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "power bi": "Power BI", "powerbi": "Power BI",
    "pytorch": "PyTorch",
    "rag": "RAG",
    "react": "React", "react.js": "React", "reactjs": "React",
    "rest api": "REST API", "restful api": "REST API",
    "saas": "SaaS",
    "sdk": "SDK",
    "apache spark": "Spark", "pyspark": "Spark", "spark": "Spark",
    "sql": "SQL",
    "tensorflow": "TensorFlow",
    "ts": "TypeScript", "typescript": "TypeScript",
    "ui": "UI",
    "ux": "UX",
}


def normalize_skill(raw_skill: str) -> str:
    """One raw spelling in, agreed canonical name out. Unknown skills
    fall through to a tidied version of themselves rather than being
    dropped, so nothing is silently lost."""
    key = raw_skill.strip().lower()
    return SKILL_ALIASES.get(key, _initcap(raw_skill.strip()))


def merge_skills(key_skills: list[str] | None, tech_in_desc: list[str] | None) -> list[str]:
    """Merge both skill sources, normalize each, drop duplicates."""
    combined = (key_skills or []) + (tech_in_desc or [])
    seen = set()
    result = []
    for s in combined:
        if not s.strip():
            continue
        norm = normalize_skill(s)
        if norm not in seen:
            seen.add(norm)
            result.append(norm)
    return sorted(result)


# =====================================================================
# RANGE PARSING — pull two numbers out of strings like:
#     "6 - 10 years"    -> 6, 10
#     "15-25 Lacs P.A." -> 15, 25
#     "5+ years"        -> 5, None
# Returns (None, None) when nothing numeric is present ("Not Disclosed").
# =====================================================================
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")
_PLUS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+")


def parse_range_min(raw: str | None) -> float | None:
    if raw is None:
        return None
    m = _RANGE_RE.search(raw)
    if m:
        return float(m.group(1))
    m = _PLUS_RE.search(raw)
    if m:
        return float(m.group(1))
    return None


def parse_range_max(raw: str | None) -> float | None:
    if raw is None:
        return None
    m = _RANGE_RE.search(raw)
    if m:
        return float(m.group(2))
    return None  # "5+ years" has no upper bound; don't invent one


def _round_half_up(x: float) -> int:
    """Matches Postgres's NUMERIC::INT cast (round half away from
    zero), which Python's built-in round() doesn't guarantee (it rounds
    half to even). Experience values are virtually always whole numbers
    in the source text, so this only ever matters at the margin."""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


# =====================================================================
# EMPLOYMENT / CONTRACT TYPE — split "Full Time, Permanent" on commas,
# title-case each part, match against a known vocabulary. First match
# in the original left-to-right order wins.
# =====================================================================
EMPLOYMENT_TYPES = {"full time", "full-time", "part time", "part-time"}
CONTRACT_TYPES = {"permanent", "contract", "contractual", "temporary", "internship", "freelance"}

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _initcap(s: str) -> str:
    """Postgres initcap(): uppercase the first character of each run of
    alphanumeric characters, lowercase the rest. Digits do NOT start a
    new word — initcap('j2ee') is 'J2ee', not 'J2Ee' — so word runs
    include digits and only the run's first character changes case."""
    return _WORD_RE.sub(lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), s)


def _match_type(raw: str | None, vocabulary: set[str]) -> str | None:
    for part in (raw or "").split(","):
        capped = _initcap(part.strip())
        if capped.lower() in vocabulary:
            return capped
    return None


def parse_employment_type(raw: str | None) -> str | None:
    return _match_type(raw, EMPLOYMENT_TYPES)


def parse_contract_type(raw: str | None) -> str | None:
    return _match_type(raw, CONTRACT_TYPES)


# =====================================================================
# WORKING TYPE — Naukri only shows a badge for Hybrid/Remote/WFH
# postings; no badge means on-site, not unknown.
# =====================================================================
def normalize_working_type(raw: str | None) -> str:
    text = (raw or "").lower()
    if "hybrid" in text:
        return "Hybrid"
    if "remote" in text:
        return "Remote"
    if "work from home" in text:
        return "Remote"
    if "office" in text:
        return "On-site"
    return "On-site"


# =====================================================================
# LOCATION -> CITIES — split "Hyderabad, Chennai, Remote" on commas,
# resolve each fragment through the alias table, keep unmatched
# fragments visible rather than dropping them.
# =====================================================================
CITY_ALIASES = {
    "ahmedabad": "Ahmedabad",
    "bangalore": "Bengaluru", "bangalore rural": "Bengaluru",
    "bengaluru": "Bengaluru", "bengaluru rural": "Bengaluru",
    "bhubaneswar": "Bhubaneswar",
    "chandigarh": "Chandigarh",
    "chennai": "Chennai",
    "coimbatore": "Coimbatore",
    "delhi": "Delhi", "new delhi": "Delhi",
    "delhi / ncr": "Delhi / NCR", "delhi/ncr": "Delhi / NCR", "ncr": "Delhi / NCR",
    "faridabad": "Faridabad",
    "ghaziabad": "Ghaziabad",
    "greater noida": "Greater Noida",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "hyderabad": "Hyderabad",
    "indore": "Indore",
    "jaipur": "Jaipur",
    "cochin": "Kochi", "ernakulam": "Kochi", "kochi": "Kochi",
    "kolkata": "Kolkata",
    "mumbai": "Mumbai", "mumbai (all areas)": "Mumbai",
    "navi mumbai": "Mumbai", "thane": "Mumbai",
    "nizamabad": "Nizamabad",
    "noida": "Noida",
    "pune": "Pune",
    "secunderabad": "Secunderabad",
    "thiruvananthapuram": "Thiruvananthapuram", "trivandrum": "Thiruvananthapuram",
    "visakhapatnam": "Visakhapatnam", "vizag": "Visakhapatnam",
    "warangal": "Warangal",
}


def resolve_locations(raw_location: str | None, city_name_to_id: dict[str, int]) -> tuple[list[int], list[str]]:
    """Returns (city_ids, unmapped_fragments)."""
    city_ids: set[int] = set()
    unmapped: list[str] = []

    for frag in (raw_location or "").split(","):
        frag = frag.strip()
        if not frag:
            continue
        key = frag.lower()
        city_name = CITY_ALIASES.get(key)
        if city_name:
            city_ids.add(city_name_to_id[city_name])
        elif key != "india" and "remote" not in key:
            unmapped.append(frag)

    return sorted(city_ids), unmapped


# =====================================================================
# ROLE CLASSIFICATION — ported from role_patterns. Lower priority
# number wins; most-specific patterns are given the lowest numbers so
# they beat generic catch-alls like "engineer".
# =====================================================================
# Listed in the exact order roles_setup.sql originally inserted them.
# That order matters: for two patterns tied on priority, Postgres's
# `ORDER BY priority LIMIT 1` (no secondary sort key) resolved ties by
# table scan order, which followed insertion order on this untouched
# table. A Python-side re-sort that didn't preserve this (e.g. sorting
# ties alphabetically) picks a different winner on real, ambiguous
# titles like "Data Scientist | Data Engineer (...)" — verified against
# live data, this is not a hypothetical.
_ROLE_PATTERNS_SOURCE = [
    ("machine learning", "ML Engineer", 10),
    ("ml engineer", "ML Engineer", 10),
    ("mlops", "ML Engineer", 10),
    ("deep learning", "ML Engineer", 10),
    ("data scientist", "Data Scientist", 10),
    ("data science", "Data Scientist", 11),
    ("ai engineer", "AI Engineer", 10),
    ("artificial intelligence", "AI Engineer", 11),
    ("generative ai", "AI Engineer", 10),
    ("gen ai", "AI Engineer", 10),
    ("nlp", "AI Engineer", 11),
    ("data engineer", "Data Engineer", 10),
    ("etl developer", "Data Engineer", 10),
    ("big data", "Data Engineer", 12),
    ("data analyst", "Data Analyst", 10),
    ("business intelligence", "Data Analyst", 11),
    ("bi developer", "Data Analyst", 10),
    ("data architect", "Architect", 10),
    ("devops", "DevOps Engineer", 10),
    ("site reliability", "DevOps Engineer", 10),
    ("sre", "DevOps Engineer", 11),
    ("platform engineer", "DevOps Engineer", 11),
    ("cloud engineer", "Cloud Engineer", 10),
    ("cloud architect", "Architect", 10),
    ("infrastructure", "Infrastructure", 12),
    ("network engineer", "Infrastructure", 11),
    ("system administrator", "Infrastructure", 11),
    ("full stack", "Full Stack Developer", 10),
    ("fullstack", "Full Stack Developer", 10),
    ("frontend", "Frontend Developer", 10),
    ("front end", "Frontend Developer", 10),
    ("ui developer", "Frontend Developer", 10),
    ("react developer", "Frontend Developer", 10),
    ("angular developer", "Frontend Developer", 10),
    ("backend", "Backend Developer", 10),
    ("back end", "Backend Developer", 10),
    ("java developer", "Backend Developer", 11),
    ("python developer", "Backend Developer", 11),
    (".net developer", "Backend Developer", 11),
    ("node developer", "Backend Developer", 11),
    ("android developer", "Mobile Developer", 10),
    ("ios developer", "Mobile Developer", 10),
    ("mobile developer", "Mobile Developer", 10),
    ("flutter", "Mobile Developer", 11),
    ("qa engineer", "QA / Test", 10),
    ("test engineer", "QA / Test", 10),
    ("automation test", "QA / Test", 10),
    ("quality assurance", "QA / Test", 11),
    ("sdet", "QA / Test", 10),
    ("security engineer", "Security", 10),
    ("cyber security", "Security", 10),
    ("information security", "Security", 11),
    ("business analyst", "Business Analyst", 10),
    ("product manager", "Product", 10),
    ("product owner", "Product", 10),
    ("scrum master", "Product", 11),
    ("solution architect", "Architect", 10),
    ("technical architect", "Architect", 10),
    ("architect", "Architect", 14),
    ("engineering manager", "Engineering Manager", 10),
    ("tech lead", "Engineering Manager", 11),
    ("software engineer", "Software Engineer", 20),
    ("software developer", "Software Engineer", 20),
    ("developer", "Software Engineer", 25),
    ("engineer", "Software Engineer", 30),
    ("analyst", "Business Analyst", 30),
    ("consultant", "Consultant", 28),
    # Added directly to the live table at some point, not present in
    # roles_setup.sql — reads like leftover test data ("some phrase" is
    # not a real job-title fragment). Kept last, matching its later
    # insertion, and ported faithfully rather than silently dropped.
    ("some phrase", "Data Scientist", 10),
]

# Stable sort: for equal priority, order is unchanged from the list
# above — i.e. insertion order — which is what reproduces the tie
# behavior actually observed in the live SQL function.
ROLE_PATTERNS = sorted(_ROLE_PATTERNS_SOURCE, key=lambda row: row[2])


def classify_role(raw_title: str | None) -> str:
    title = (raw_title or "").lower()
    for pattern, family, _priority in ROLE_PATTERNS:
        if pattern in title:
            return family
    return "Other"


# =====================================================================
# ORCHESTRATION
# =====================================================================
UPSERT_SQL = """
INSERT INTO cleaned_postings (
    job_id, url, title, company,
    experience_min, experience_max, salary_min, salary_max,
    skills, city_ids, working_type, employment_type, contract_type,
    unmapped_locations, posted_date, openings,
    first_seen_date, last_seen_date, times_seen, role_family
) VALUES %s
ON CONFLICT (job_id) DO UPDATE SET
    url                = EXCLUDED.url,
    title              = EXCLUDED.title,
    company            = EXCLUDED.company,
    experience_min     = EXCLUDED.experience_min,
    experience_max     = EXCLUDED.experience_max,
    salary_min         = EXCLUDED.salary_min,
    salary_max         = EXCLUDED.salary_max,
    skills             = EXCLUDED.skills,
    city_ids           = EXCLUDED.city_ids,
    working_type       = EXCLUDED.working_type,
    employment_type    = EXCLUDED.employment_type,
    contract_type      = EXCLUDED.contract_type,
    unmapped_locations = EXCLUDED.unmapped_locations,
    posted_date        = EXCLUDED.posted_date,
    openings           = EXCLUDED.openings,
    last_seen_date     = EXCLUDED.last_seen_date,
    times_seen         = EXCLUDED.times_seen,
    role_family        = EXCLUDED.role_family;
"""


def clean_and_populate() -> int:
    """Read raw_postings, compute every cleaned field in Python, write
    cleaned_postings and posting_cities. Safe to run repeatedly."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT city_id, city_name FROM cities")
            city_name_to_id = {row["city_name"]: row["city_id"] for row in cur.fetchall()}

            cur.execute("SELECT * FROM raw_postings")
            raw_rows = cur.fetchall()

        rows = []
        posting_city_pairs = []

        for r in raw_rows:
            exp_min = parse_range_min(r["experience"])
            exp_max = parse_range_max(r["experience"])
            city_ids, unmapped = resolve_locations(r["location"], city_name_to_id)

            rows.append((
                r["job_id"], r["url"], r["title"], r["company"],
                _round_half_up(exp_min) if exp_min is not None else None,
                _round_half_up(exp_max) if exp_max is not None else None,
                parse_range_min(r["salary"]),
                parse_range_max(r["salary"]),
                merge_skills(r["key_skills"], r["tech_in_desc"]),
                city_ids,
                normalize_working_type(r["working_type"]),
                parse_employment_type(r["employment_type"]),
                parse_contract_type(r["employment_type"]),
                unmapped,
                r["posted_date"], r["openings"],
                r["first_seen_date"], r["last_seen_date"], r["times_seen"],
                classify_role(r["title"]),
            ))

            for city_id in city_ids:
                posting_city_pairs.append((r["job_id"], city_id))

        with conn.cursor() as cur:
            if rows:
                execute_values(cur, UPSERT_SQL, rows)

            # Rebuild the city links — cheap at this scale, avoids stale
            # rows when a posting's location changes between scrapes.
            cur.execute("""
                DELETE FROM posting_cities
                WHERE job_id IN (SELECT job_id FROM cleaned_postings)
            """)
            if posting_city_pairs:
                execute_values(
                    cur,
                    "INSERT INTO posting_cities (job_id, city_id) VALUES %s ON CONFLICT DO NOTHING",
                    posting_city_pairs,
                )

        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    affected = clean_and_populate()
    print(f"clean_and_populate: {affected} row(s) processed.")
