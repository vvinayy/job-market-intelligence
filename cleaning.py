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

from skill_taxonomy import SKILL_ALIASES as _TAXONOMY_ALIASES, extract_certifications


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


def make_description_hash(description: str | None) -> str | None:
    """Groups postings that share the same underlying description text
    even when posted under different companies/fingerprints — common on
    Naukri when several staffing agencies repost the exact same vacancy.
    Not fuzzy matching, just exact-text-after-normalizing — genuinely
    reworded reposts won't be caught, only verbatim copies. Returns None
    for an empty description rather than hashing an empty string, so
    postings with no description don't all collide into one fake group."""
    if not description:
        return None
    return hashlib.sha256(_normalize_for_fingerprint(description).encode()).hexdigest()


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
# SENIORITY — inferred from the title only, a different (and often
# absent) signal from role_family. Most Indian IT titles carry no
# seniority marker at all ("Python Developer", not "Senior Python
# Developer") — that's the honest, common case, so this returns None
# rather than guessing a default the way classify_role() falls back to
# "Other". A title with no marker means "not stated", not "mid-level".
# =====================================================================
_SENIORITY_PATTERNS_SOURCE = [
    ("intern", "Intern/Trainee", 10),
    ("internship", "Intern/Trainee", 10),
    ("trainee", "Intern/Trainee", 10),
    ("fresher", "Intern/Trainee", 10),
    ("principal", "Lead/Principal", 10),
    ("staff engineer", "Lead/Principal", 10),
    ("tech lead", "Lead/Principal", 10),
    ("technical lead", "Lead/Principal", 10),
    ("director", "Manager/Leadership", 10),
    ("vice president", "Manager/Leadership", 10),
    ("head of", "Manager/Leadership", 10),
    ("engineering manager", "Manager/Leadership", 10),
    ("project manager", "Manager/Leadership", 11),
    ("manager", "Manager/Leadership", 15),
    ("lead", "Lead/Principal", 20),
    ("senior", "Senior", 25),
    ("sr.", "Senior", 25),
    ("junior", "Junior", 25),
    ("jr.", "Junior", 25),
    ("associate", "Associate", 30),
]

SENIORITY_PATTERNS = sorted(_SENIORITY_PATTERNS_SOURCE, key=lambda row: row[2])


def classify_seniority(raw_title: str | None) -> str | None:
    title = (raw_title or "").lower()
    for pattern, level, _priority in SENIORITY_PATTERNS:
        if pattern in title:
            return level
    return None


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
# EDUCATION DEGREES / SPECIALIZATIONS — breaks parse_qualifications()'s
# flat "field_of_study" display string into individually referenceable
# facts: which degrees does a posting accept, and which specialization
# for each. Confirmed from real HTML that Naukri renders this as ONE
# flattened <span> per level (no separate chip per degree), e.g.
#   "MCA in Any Specialization, MS/M.Sc(Science) in Any Specialization,
#    M.Tech in Any Specialization"
# so the comma is genuinely overloaded in the source text itself — it
# separates a new degree from the one before it, AND separates two
# specializations that both belong to the same degree ("B.Sc in
# Computer Science and Technology, Information Technology (IT)" is ONE
# degree with two acceptable specializations, not two degrees). There
# is no punctuation that tells the two cases apart, so this walks the
# comma-split tokens and treats anything that isn't a recognized degree
# name as another specialization for whichever degree came right before
# it. The base vocabulary came from every distinct field_of_study value
# in this project's own data; it was later cross-checked against
# Naukri's own site-wide Education filter panel (the full checkbox list
# on a search results page) and extended with entries confirmed IT-
# relevant there but not yet seen in our own sample (CA, PG Diploma,
# Post Graduation Not Required, B.B.A./B.M.S., M.Com/B.Com, M.A) —
# Naukri's own vocabulary, not a guess, just not something our own
# scrapes had produced yet. Entries with no realistic path into an IT
# search (Medical-MS/MD, M.B.B.S., B.Ed, B.Arch, LLB) were deliberately
# left out; "Diploma" was also left out despite appearing on Naukri's
# list, since it carries no clear UG/PG signal there — better caught by
# parse_education_degrees()'s auto-registration fallback with real
# context than assigned a guessed level here.
#
# A slash-joined entry ("B.Tech / B.E.", "MS/M.Sc(Science)", "MBA/PGDM",
# "Ph.D/Doctorate", "B.B.A. / B.M.S.") is Naukri listing two alternative
# credentials, not one combined one — split into separate atomic degree
# references.
# =====================================================================
_DEGREE_VOCABULARY = [
    # (raw prefix exactly as Naukri renders it, level)
    ("B.Tech / B.E.", "UG"),
    ("B.C.A.", "UG"),
    ("B.Sc", "UG"),
    ("B.A - Bachelor of Arts", "UG"),
    ("B.B.A. / B.M.S.", "UG"),
    ("B.Com", "UG"),
    ("Any Graduate", "UG"),
    ("Other Graduate", "UG"),
    ("Graduation Not Required", "UG"),
    ("MS/M.Sc(Science)", "PG"),
    ("MBA/PGDM", "PG"),
    ("M.Tech", "PG"),
    ("MCA", "PG"),
    ("MCM", "PG"),
    ("LLM", "PG"),
    ("M.Com", "PG"),
    ("M.A", "PG"),
    ("CA", "PG"),
    ("PG Diploma", "PG"),
    ("Any Postgraduate", "PG"),
    ("Post Graduation Not Required", "PG"),
    ("Ph.D/Doctorate", "Doctorate"),
    ("Any Doctorate", "Doctorate"),
    ("Doctorate Not Required", "Doctorate"),
]
# Longest prefix first, so a shorter entry can never shadow a longer
# one that starts the same way.
_DEGREE_VOCABULARY.sort(key=lambda row: -len(row[0]))

_SPECIALIZATION_ALIASES = {
    "information technology (it)": "Information Technology",
}


def _split_degree_names(prefix: str) -> list[str]:
    """'B.Tech / B.E.' -> ['B.Tech', 'B.E.'] (two alternative degrees).
    'B.A - Bachelor of Arts' -> ['B.A'] (one degree, drop the redundant
    expansion). Everything else is already atomic."""
    if "/" in prefix:
        return [p.strip() for p in prefix.split("/")]
    if " - " in prefix:
        return [prefix.split(" - ")[0].strip()]
    return [prefix]


def _normalize_specialization(raw: str) -> str:
    text = re.sub(r"\bAnd\b", "and", raw.strip())
    return _SPECIALIZATION_ALIASES.get(text.lower(), text)


def _parse_field_of_study(raw_text: str | None, fallback_level: str | None = None) -> list[dict]:
    """One level's flattened text in ('MCA in Any Specialization, M.Tech
    in Any Specialization'), one dict per atomic degree out. Each dict
    is {"degree": str, "level": "UG"/"PG"/"Doctorate",
    "specializations": [str, ...]}.

    A token matching no known degree is either a continuation
    specialization for whichever degree came right before it, or — if
    nothing came before it — a degree phrasing this taxonomy hasn't seen
    yet. That second case is registered as a new degree rather than
    dropped, same "fall through to a tidied version of itself instead of
    being silently lost" rule normalize_skill() uses for an unrecognized
    skill. fallback_level supplies its UG/PG/Doctorate classification —
    Naukri's own label for this block, passed in by
    parse_education_degrees() — since that's real signal even when the
    degree name itself is new. Naukri's "<Degree> in <Specialization>"
    phrasing is assumed to hold for an unrecognized token too, splitting
    on the first " in "."""
    if not raw_text:
        return []

    tokens = [t.strip() for t in raw_text.split(",") if t.strip()]
    groups: list[dict] = []  # [{"names": [...], "level": ..., "specializations": [...]}]

    for token in tokens:
        matched = next(
            ((prefix, level) for prefix, level in _DEGREE_VOCABULARY
             if token == prefix or token.startswith(prefix + " in ")),
            None,
        )
        if matched:
            prefix, level = matched
            spec_text = token[len(prefix):].strip()
            if spec_text.startswith("in "):
                spec_text = spec_text[3:].strip()
            specializations = [_normalize_specialization(spec_text)] if spec_text else []
            groups.append({"names": _split_degree_names(prefix), "level": level,
                            "specializations": specializations})
        elif groups:
            # Doesn't match any known degree -- another specialization
            # tacked onto whichever degree came right before it.
            groups[-1]["specializations"].append(_normalize_specialization(token))
        elif fallback_level:
            if " in " in token:
                degree_part, spec_part = token.split(" in ", 1)
                specializations = [_normalize_specialization(spec_part.strip())]
            else:
                degree_part, specializations = token, []
            groups.append({"names": _split_degree_names(degree_part.strip()), "level": fallback_level,
                            "specializations": specializations})
        # else: no fallback_level available (a direct call with no block
        # context) and nothing to attach to -- nothing safe to do.

    return [
        {"degree": name, "level": g["level"], "specializations": g["specializations"]}
        for g in groups for name in g["names"]
    ]


def parse_education_degrees(education: dict[str, str] | None) -> list[dict]:
    """Same raw {"UG": "...", "PG": "...", ...} block parse_qualifications()
    reads, broken down further into individually referenceable degree
    facts instead of one flat display string per level. The dict's own
    key (Naukri's UG/PG/Doctorate label) is passed through as
    fallback_level so a degree name this taxonomy has never seen still
    gets classified correctly instead of being dropped."""
    if not isinstance(education, dict):
        return []
    degrees = []
    for level, text in education.items():
        level_key = level.strip().lower()
        fallback_level = _QUALIFICATION_LEVEL_ALIASES.get(level_key, level.strip())
        degrees += _parse_field_of_study(text, fallback_level=fallback_level)
    return degrees


# =====================================================================
# DESCRIPTION SECTIONS — best-effort split into Responsibilities and
# Requirements, using common heading phrases. This is text heuristics,
# not guaranteed: a posting phrased unusually, or with no headings at
# all, just won't split — the full text still lives in `description`
# either way, so nothing is lost if the heuristic misses.
# =====================================================================
_RESPONSIBILITY_HEADERS = [
    "roles and responsibilities", "role and responsibilities",
    "key responsibilities", "responsibilities", "job responsibilities",
    "what you'll do", "what you will do",
]
_REQUIREMENT_HEADERS = [
    "desired candidate profile", "required skills", "requirements",
    "qualifications", "must have", "skills required", "who you are",
    "what we're looking for", "what we are looking for",
]
# A short, mostly-alphabetic standalone line — the shape a heading takes
# once HTML has been flattened to plain text (no more <h2>/<strong> tags
# to lean on).
_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z /&']{2,60}:?$")


def split_description_sections(description: str | None) -> dict[str, str | None]:
    if not description:
        return {"responsibilities": None, "requirements": None}

    buckets: dict[str, list[str]] = {"responsibilities": [], "requirements": []}
    current = None
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if _HEADER_LINE_RE.match(line):
            header = line.rstrip(":").strip().lower()
            if any(h in header for h in _RESPONSIBILITY_HEADERS):
                current = "responsibilities"
                continue
            if any(h in header for h in _REQUIREMENT_HEADERS):
                current = "requirements"
                continue
            # An unrecognized heading (e.g. "About Company") ends
            # whatever section was open, without starting a new one.
            current = None
            continue
        if current and line:
            buckets[current].append(line)

    return {
        "responsibilities": "\n".join(buckets["responsibilities"]) or None,
        "requirements": "\n".join(buckets["requirements"]) or None,
    }


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

    # Preferred (starred) skills are a subset of key_skills by raw text —
    # normalize the same way normalize_skill() normalizes everything
    # else, then intersect with the final merged list, so a preferred
    # skill here is always guaranteed to be a member of `skills` too.
    preferred_raw = raw.get("preferred_key_skills") if isinstance(raw.get("preferred_key_skills"), list) else []
    preferred_normalized = {normalize_skill(s) for s in preferred_raw if s.strip()}
    preferred_skills = [s for s in skills if s in preferred_normalized]

    title = _clean(raw.get("title"))
    company = _clean(raw.get("company"))
    description = _clean(raw.get("description"))
    sections = split_description_sections(description)

    company_rating_raw = _clean(raw.get("company_rating"))
    company_rating = float(company_rating_raw) if company_rating_raw else None

    posting = {
        "fingerprint": make_fingerprint(company, title, _clean(raw.get("location")), experience),
        "url": raw.get("url"),
        "title": title,
        "company": company,
        "description": description,
        "description_hash": make_description_hash(description),
        "responsibilities_text": sections["responsibilities"],
        "requirements_text": sections["requirements"],
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
        "seniority_level": classify_seniority(title),
        "role_category": _clean(raw.get("role_category")),
        "naukri_role": _clean(raw.get("naukri_role")),
        "industry_type": _clean(raw.get("industry_type")),
        "department": _clean(raw.get("department")),
        "posted_date": _clean(raw.get("posted_date")),
        "posted_raw": _clean(raw.get("posted_raw")),
        "openings": raw.get("openings") if isinstance(raw.get("openings"), int) else None,
        "applicant_count": raw.get("applicant_count") if isinstance(raw.get("applicant_count"), int) else None,
        "applicant_count_qualifier": _clean(raw.get("applicant_count_qualifier")),
        "company_rating": company_rating,
        "company_reviews": raw.get("company_reviews") if isinstance(raw.get("company_reviews"), int) else None,
        "company_badges": raw.get("company_badges") if isinstance(raw.get("company_badges"), list) else [],
        "source_search": _clean(raw.get("source_search")),
        "certifications": extract_certifications(description) if description else [],
    }

    return {
        "posting": posting,
        "skills": skills,
        "preferred_skills": preferred_skills,
        "qualifications": parse_qualifications(raw.get("education")),
        "qualification_degrees": parse_education_degrees(raw.get("education")),
    }
