"""
Cleaning layer — one scraped record in, a cleaned record out. Pure
Python, no PL/pgSQL.

Called in-process by the scraper (job_database.py), right after each
posting is scraped — there is no raw_postings table and no separate
batch step. clean_record() is the single entry point: it takes one
record shaped like naukri_collector.py's scrape_job_detail() output
and returns everything needed to write it — the cleaned_postings row,
plus the derived (normalized) skill names and qualifications.
job_database.py is responsible for turning skill names into skill_ids
against the skills dictionary table, using categorize_skill() below
only as the initial category guess for a skill it hasn't seen before.

fingerprinting also lives here now (moved from job_database.py, which
is now purely a database-I/O layer) since deciding "is this the same
posting as one we've already seen" is a cleaning decision, not a
storage one.
"""

import re
import math
import hashlib

from skill_taxonomy import SKILL_ALIASES as _TAXONOMY_ALIASES


NOT_FOUND = "not found"


_HAS_CONTENT_RE = re.compile(r"[A-Za-z0-9]")


def _clean(value):
    """Scraper fields use the string "not found" (and sometimes an
    empty value) as a sentinel for "this label wasn't on the page".
    Convert that into a real absence (None) so the database holds NULL
    instead of a magic string, and downstream code doesn't need to
    know about the scraper's sentinel convention.

    Also treats a string with no actual letters or digits (a bare ","
    or "-") as absent — a selector occasionally grabs a separator or
    punctuation-only fragment instead of real content, and that's not
    a value worth keeping any more than "not found" is."""
    if value in (None, NOT_FOUND, ""):
        return None
    if isinstance(value, str) and not _HAS_CONTENT_RE.search(value):
        return None
    return value


# =====================================================================
# FINGERPRINTING — same fields always produce the same fingerprint, on
# any machine, which is what makes it usable as "have I seen this job
# before?". Experience is included deliberately: large employers post
# many openings sharing a title and city, and without experience in
# the mix they'd all collapse into one row.
# =====================================================================
def _normalize_for_fingerprint(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.lower().split())


def make_fingerprint(company: str | None, title: str | None,
                      location: str | None, experience: str | None) -> str:
    combined = (
        f"{_normalize_for_fingerprint(company)}|{_normalize_for_fingerprint(title)}"
        f"|{_normalize_for_fingerprint(location)}|{_normalize_for_fingerprint(experience)}"
    )
    return hashlib.sha256(combined.encode()).hexdigest()


# =====================================================================
# SKILL NORMALIZATION — ported from skill_aliases.
# =====================================================================
SKILL_ALIASES = {
    "ai": "AI", "artificial intelligence": "AI",
    "agentic ai": "Agentic AI",
    "api": "API",
    "amazon web services": "AWS", "aws": "AWS", "aws cloud": "AWS",
    "azure": "Azure", "microsoft azure": "Azure", "azure cloud": "Azure",
    "azure databricks": "Databricks", "data bricks": "Databricks", "databricks": "Databricks",
    "aks": "AKS", "azure kubernetes service": "AKS",
    "ci cd": "CI/CD", "ci/cd": "CI/CD",
    "css": "CSS",
    "data warehousing": "Data Warehouse",
    "devops": "DevOps",
    "elt": "ELT",
    "etl": "ETL",
    "gcp": "GCP", "google cloud": "GCP", "google cloud platform": "GCP",
    "google cloud platforms": "GCP",
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
    "mlops": "MLOps",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "nlp": "NLP", "natural language processing": "NLP",
    "node": "Node.js", "node.js": "Node.js", "nodejs": "Node.js",
    "nosql": "NoSQL", "nosql databases": "NoSQL",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "power bi": "Power BI", "powerbi": "Power BI",
    "pytorch": "PyTorch",
    "rag": "RAG",
    "react": "React", "react.js": "React", "reactjs": "React",
    "rest": "REST API", "rest api": "REST API", "restful api": "REST API",
    "saas": "SaaS",
    "sdk": "SDK",
    "apache spark": "Spark", "pyspark": "Spark", "spark": "Spark",
    "sql": "SQL",
    "tensorflow": "TensorFlow",
    "ts": "TypeScript", "typescript": "TypeScript",
    "ui": "UI",
    "ux": "UX",
    "apis": "API",
    "generative ai": "Generative AI",
}


def _build_taxonomy_alias_lookup() -> dict[str, str]:
    """Flatten skill_taxonomy.py's canonical -> [spelling variants]
    table into alias -> canonical, so key_skills (Naukri's own tags)
    resolves to the same canonical spelling that tech_in_description
    already gets via skill_taxonomy.extract_skills(). Without this, the
    same tool could land in the skills table under two different
    casings depending on which source mentioned it — "Fastapi" from a
    Naukri tag, "FastAPI" from the mined description text — which is
    why so many high-frequency skills were sitting uncategorized: the
    category lookup is an exact-match dict, and neither spelling was
    wrong exactly, they just didn't match SKILL_CATEGORIES' key.

    A couple of skill_taxonomy aliases are genuine regex patterns, not
    fixed spellings (e.g. "angular \\d+\\+?" matches any version
    number) — those can't become a literal dict key, so they're
    skipped. Everything else is just escaped punctuation (c\\+\\+,
    \\.net) and is recovered by stripping the backslashes back out.
    """
    lookup = {}
    for canonical, aliases in _TAXONOMY_ALIASES.items():
        for alias in aliases:
            if "\\d" in alias:
                continue
            lookup[alias.replace("\\", "").lower()] = canonical
    return lookup


# cleaning.py's own SKILL_ALIASES wins on any collision — it's the more
# specific, more carefully-cased list for this project.
_ALL_SKILL_ALIASES = {**_build_taxonomy_alias_lookup(), **SKILL_ALIASES}


def normalize_skill(raw_skill: str) -> str:
    """One raw spelling in, agreed canonical name out. Unknown skills
    fall through to a tidied version of themselves rather than being
    dropped, so nothing is silently lost."""
    key = raw_skill.strip().lower()
    return _ALL_SKILL_ALIASES.get(key, _initcap(raw_skill.strip()))


def merge_skills(key_skills: list[str] | None, tech_in_desc: list[str] | None) -> list[str]:
    """Merge both skill sources (Naukri's own Key Skills chips and
    skills found by scanning the description), normalize each, drop
    duplicates."""
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
# SKILL CATEGORIES — Frontend / Backend / Database / Cloud-DevOps /
# Data-ML / Testing / Languages. Covers the canonical names produced by
# both SKILL_ALIASES above and skill_taxonomy.py's larger vocabulary
# (that's what actually populates key_skills/tech_in_description, so
# this needs to cover its canonical names too, not just this file's).
# Anything not listed here gets category=None rather than a forced
# guess — an uncategorized skill is honest; a wrongly categorized one
# isn't.
# =====================================================================
SKILL_CATEGORIES = {
    # Languages
    "Python": "Languages", "Java": "Languages", "C": "Languages", "C++": "Languages",
    "C#": "Languages", "Go": "Languages", "Rust": "Languages", "Ruby": "Languages",
    "PHP": "Languages", "Scala": "Languages", "Kotlin": "Languages", "Swift": "Languages",
    "R": "Languages", "SQL": "Languages", "Shell scripting": "Languages", "PowerShell": "Languages",

    # Frontend
    "React": "Frontend", "Angular": "Frontend", "Vue": "Frontend", "Next.js": "Frontend",
    "HTML": "Frontend", "CSS": "Frontend", "Tailwind": "Frontend", "Bootstrap": "Frontend",
    "jQuery": "Frontend", "Redux": "Frontend", "JavaScript": "Frontend", "TypeScript": "Frontend",
    "UI": "Frontend", "UX": "Frontend",

    # Backend
    "Node.js": "Backend", "Express": "Backend", "Django": "Backend", "Flask": "Backend",
    "FastAPI": "Backend", "Spring Boot": "Backend", "Spring": "Backend", ".NET": "Backend",
    ".NET Core": "Backend", "ASP.NET": "Backend", "Entity Framework": "Backend", "MVC": "Backend",
    "LINQ": "Backend", "Web API": "Backend", "REST API": "Backend", "GraphQL": "Backend",
    "gRPC": "Backend", "Microservices": "Backend", "API": "Backend", "SDK": "Backend",

    # Database
    "PostgreSQL": "Database", "MySQL": "Database", "MongoDB": "Database", "Redis": "Database",
    "Oracle": "Database", "SQL Server": "Database", "Cassandra": "Database",
    "Elasticsearch": "Database", "DynamoDB": "Database", "Snowflake": "Database", "NoSQL": "Database",

    # Cloud / DevOps
    "AWS": "Cloud/DevOps", "Azure": "Cloud/DevOps", "GCP": "Cloud/DevOps", "Lambda": "Cloud/DevOps",
    "S3": "Cloud/DevOps", "EC2": "Cloud/DevOps", "Docker": "Cloud/DevOps", "Kubernetes": "Cloud/DevOps",
    "Terraform": "Cloud/DevOps", "Ansible": "Cloud/DevOps", "Jenkins": "Cloud/DevOps",
    "CI/CD": "Cloud/DevOps", "Git": "Cloud/DevOps", "GitHub": "Cloud/DevOps", "GitLab": "Cloud/DevOps",
    "Linux": "Cloud/DevOps", "Nginx": "Cloud/DevOps", "RabbitMQ": "Cloud/DevOps", "DevOps": "Cloud/DevOps",
    "Grafana": "Cloud/DevOps", "AKS": "Cloud/DevOps", "Containerization": "Cloud/DevOps",
    "Version Control": "Cloud/DevOps",

    # Data / ML
    "Pandas": "Data/ML", "NumPy": "Data/ML", "TensorFlow": "Data/ML", "PyTorch": "Data/ML",
    "scikit-learn": "Data/ML", "LangChain": "Data/ML", "Power BI": "Data/ML", "Tableau": "Data/ML",
    "Microsoft Fabric": "Data/ML", "Databricks": "Data/ML", "Synapse": "Data/ML",
    "Data Factory": "Data/ML", "dbt": "Data/ML", "Looker": "Data/ML", "BigQuery": "Data/ML",
    "Redshift": "Data/ML", "Delta Lake": "Data/ML", "Data Warehouse": "Data/ML", "ETL": "Data/ML",
    "ELT": "Data/ML", "Solr": "Data/ML", "OpenSearch": "Data/ML", "Lucene": "Data/ML",
    "Kafka": "Data/ML", "Airflow": "Data/ML", "Spark": "Data/ML", "Hadoop": "Data/ML",
    "Machine Learning": "Data/ML", "AI": "Data/ML", "NLP": "Data/ML", "LLM": "Data/ML",
    "RAG": "Data/ML", "IoT": "Data/ML", "Generative AI": "Data/ML", "Deep Learning": "Data/ML",
    "Agentic AI": "Data/ML",
    "Data Science": "Data/ML", "Data Engineering": "Data/ML", "Data Analytics": "Data/ML",
    "Data Analysis": "Data/ML", "Data Modeling": "Data/ML", "Big Data": "Data/ML",
    "Hive": "Data/ML", "Data Lake": "Data/ML", "Data Pipelines": "Data/ML",
    "Data Quality": "Data/ML", "Data Visualization": "Data/ML", "Statistical Modeling": "Data/ML",
    "Predictive Modeling": "Data/ML", "MLOps": "Data/ML",

    # Testing
    "NUnit": "Testing", "JUnit": "Testing", "pytest": "Testing", "Selenium": "Testing",
    "Jest": "Testing",
}


def categorize_skill(skill: str) -> str | None:
    return SKILL_CATEGORIES.get(skill)


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
    """Round half away from zero, matching Postgres's old NUMERIC::INT
    cast behavior — Python's round() rounds half to even instead.
    Experience values are virtually always whole numbers in the source
    text, so this only ever matters at the margin."""
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
# ROLE CLASSIFICATION — lower priority number wins; most-specific
# patterns are given the lowest numbers so they beat generic catch-alls
# like "engineer".
# =====================================================================
# For two patterns tied on priority, the first one listed wins — this
# order matches the original SQL implementation's real tie behavior
# (table scan order followed insertion order), confirmed against a
# real ambiguous title ("Data Scientist | Data Engineer (...)") that
# the two orderings classify differently. Not a hypothetical.
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
    ("some phrase", "Data Scientist", 10),
]

# Stable sort: for equal priority, order is unchanged from the list
# above — i.e. insertion order.
ROLE_PATTERNS = sorted(_ROLE_PATTERNS_SOURCE, key=lambda row: row[2])


def classify_role(raw_title: str | None) -> str:
    title = (raw_title or "").lower()
    for pattern, family, _priority in ROLE_PATTERNS:
        if pattern in title:
            return family
    return "Other"


# =====================================================================
# QUALIFICATIONS — Naukri's Education block, read by the scraper as
# {"UG": "Any Graduate", "PG": "Any Postgraduate", ...}. Not every
# posting shows all three levels (a posting with no doctorate
# requirement usually omits that row rather than saying "not
# required"), so this just normalizes whatever levels are actually
# present instead of assuming a fixed set.
# =====================================================================
_QUALIFICATION_LEVEL_ALIASES = {
    "ug": "UG", "under graduate": "UG", "undergraduate": "UG",
    "pg": "PG", "post graduate": "PG", "postgraduate": "PG",
    "doctorate": "Doctorate", "phd": "Doctorate", "ph.d": "Doctorate",
}


def parse_qualifications(education: dict[str, str] | None) -> list[dict[str, str]]:
    if not isinstance(education, dict):
        return []
    qualifications = []
    for level, field_of_study in education.items():
        level_key = level.strip().lower()
        normalized_level = _QUALIFICATION_LEVEL_ALIASES.get(level_key, level.strip())
        field_of_study = (field_of_study or "").strip()
        if normalized_level and field_of_study:
            qualifications.append({"level": normalized_level, "field_of_study": field_of_study})
    return qualifications


# =====================================================================
# clean_record() — the single entry point. Takes one scraped record
# (naukri_collector.py's scrape_job_detail() shape) plus a city-name ->
# city_id lookup, returns everything needed to write it.
# =====================================================================
def clean_record(raw: dict, city_name_to_id: dict[str, int]) -> dict:
    experience = _clean(raw.get("experience"))
    salary = _clean(raw.get("salary"))
    exp_min = parse_range_min(experience)
    exp_max = parse_range_max(experience)
    city_ids, unmapped = resolve_locations(_clean(raw.get("location")), city_name_to_id)

    key_skills = raw.get("key_skills") if isinstance(raw.get("key_skills"), list) else None
    tech_in_desc = raw.get("tech_in_description") if isinstance(raw.get("tech_in_description"), list) else None
    skills = merge_skills(key_skills, tech_in_desc)

    title = _clean(raw.get("title"))
    company = _clean(raw.get("company"))

    posting = {
        "fingerprint": make_fingerprint(company, title, _clean(raw.get("location")), experience),
        "url": raw.get("url"),
        "title": title,
        "company": company,
        "description": _clean(raw.get("description")),
        "experience_min": _round_half_up(exp_min) if exp_min is not None else None,
        "experience_max": _round_half_up(exp_max) if exp_max is not None else None,
        "salary_min": parse_range_min(salary),
        "salary_max": parse_range_max(salary),
        "city_ids": city_ids,
        "unmapped_locations": unmapped,
        "working_type": normalize_working_type(_clean(raw.get("working_type"))),
        "employment_type": parse_employment_type(_clean(raw.get("employment_type"))),
        "contract_type": parse_contract_type(_clean(raw.get("employment_type"))),
        "role_family": classify_role(title),
        "role_category": _clean(raw.get("role_category")),
        "industry_type": _clean(raw.get("industry_type")),
        "department": _clean(raw.get("department")),
        "posted_date": _clean(raw.get("posted_date")),
        "posted_raw": _clean(raw.get("posted_raw")),
        "openings": raw.get("openings") if isinstance(raw.get("openings"), int) else None,
    }

    return {
        "posting": posting,
        "skills": skills,
        "qualifications": parse_qualifications(raw.get("education")),
    }
