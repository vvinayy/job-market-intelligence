"""
Response shapes.

Defining these explicitly means the API has a documented contract: a
client knows what fields come back and what type each is, and FastAPI
generates interactive docs from them automatically.
"""

from datetime import date, datetime
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# Postings
# ---------------------------------------------------------------------
class PostingSummary(BaseModel):
    job_id: int
    title: str | None = None
    company: str | None = None
    role_family: str | None = None
    seniority_level: str | None = None
    role_category: str | None = None
    naukri_role: str | None = None
    industry_type: str | None = None
    department: str | None = None
    experience_min: int | None = None
    experience_max: int | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    skills: list[str] = []
    cities: list[str] = []
    working_type: str | None = None
    employment_type: str | None = None
    contract_type: str | None = None
    posted_date: date | None = None
    openings: int | None = None
    applicant_count: int | None = None
    applicant_count_qualifier: str | None = Field(
        None, description="'at_least', 'less_than', or null if the count was exact")
    company_rating: float | None = None
    company_reviews: int | None = None
    url: str | None = None


class Qualification(BaseModel):
    level: str
    field_of_study: str | None = None


class PostingDetail(PostingSummary):
    """Everything in the summary plus the fields only worth fetching
    for a single record — the description is large enough that returning
    it in list responses would bloat every page."""
    description: str | None = None
    responsibilities_text: str | None = None
    requirements_text: str | None = None
    certifications: list[str] = []
    preferred_skills: list[str] = []
    company_badges: list[str] = []
    source_search: str | None = None
    qualifications: list[Qualification] = []
    first_seen_date: date | None = None
    last_seen_date: date | None = None
    times_seen: int | None = None
    days_listed: int | None = None


class PostingPage(BaseModel):
    """A page of results plus the metadata a client needs to paginate:
    without `total`, a caller can't tell whether more pages exist."""
    total: int = Field(description="Matching postings, ignoring pagination")
    page: int
    page_size: int
    pages: int
    items: list[PostingSummary]


# ---------------------------------------------------------------------
# Reference data — for building filter controls
# ---------------------------------------------------------------------
class City(BaseModel):
    city_id: int
    city_name: str
    state: str
    postings: int = 0


class State(BaseModel):
    state_name: str
    cities: int = 0
    postings: int = 0


class NamedCount(BaseModel):
    name: str
    postings: int


class SkillInfo(BaseModel):
    skill: str
    postings: int
    share_pct: float


# ---------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------
class Summary(BaseModel):
    total_postings: int
    active_last_7_days: int
    new_postings_7d: int
    companies: int
    distinct_skills: int
    distinct_roles: int
    cities_covered: int
    postings_with_salary: int
    salary_disclosure_pct: float
    postings_with_education: int
    avg_skills_per_posting: float
    median_openings: float | None = None
    max_openings: int | None = None
    earliest_posting: date | None = None
    latest_posting: date | None = None


class Bucket(BaseModel):
    bucket: str
    postings: int
    share_pct: float


class SkillPair(BaseModel):
    skill_a: str
    skill_b: str
    together: int
    pct_of_skill_a: float


class SkillSuggestion(BaseModel):
    skill: str
    postings: int
    share_pct: float


# ---------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------
class Coverage(BaseModel):
    days_recorded: int
    earliest: date | None = None
    latest: date | None = None
    distinct_skills: int
    daily_delta_available: bool
    baseline_delta_available: bool


class TrendPoint(BaseModel):
    snapshot_date: date
    skill: str
    posting_count: int


class Mover(BaseModel):
    skill: str
    snapshot_date: date
    posting_count: int
    previous_count: float | None = None
    change: float | None = None
    pct_change: float | None = None
    comparison: str = Field(description="'previous_day' or 'rolling_7d'")


class FirstAppearance(BaseModel):
    skill: str
    first_seen: date
    days_present: int


# ---------------------------------------------------------------------
# Scrape health — not job data, observability on the pipeline itself
# ---------------------------------------------------------------------
class ScrapeRunSummary(BaseModel):
    run_id: int
    search_url: str
    started_at: datetime
    finished_at: datetime | None = None
    postings_found: int | None = None
    postings_scraped: int | None = None
    postings_written: int | None = None
    storage_ok: bool
    error_message: str | None = None
    duration_seconds: float | None = None


class FieldHealthWarning(BaseModel):
    field: str
    current_rate: float
    historical_avg_rate: float = Field(description="Average found-rate over the lookback window, excluding this run")


class ScrapeHealthReport(BaseModel):
    latest_run: ScrapeRunSummary | None = None
    warnings: list[FieldHealthWarning] = []
    recent_runs: list[ScrapeRunSummary] = []
