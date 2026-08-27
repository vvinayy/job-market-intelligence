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
    skills: list[str] = Field(
        default=[],
        description="Every skill this posting involves, including ones it "
                    "accepts as an alternative. Filters match against this.")
    skill_choices: list[list[str]] = Field(
        default=[],
        description="Sets the posting treats as interchangeable — any ONE "
                    "member satisfies it, e.g. [[\"AWS\",\"Azure\",\"GCP\"]]. "
                    "Skills absent from every set are required outright, so "
                    "`skills` minus these is the unconditional requirement. "
                    "Detected from the description text and best-effort: "
                    "roughly three in four sets are right, and an empty list "
                    "means none were found, not that none exist.")
    cities: list[str] = []
    working_type: str | None = None
    is_full_time: bool | None = None
    contract_type: str | None = None
    posted_date: date | None = None
    openings: int | None = None
    applicant_count: int | None = None
    applicant_count_qualifier: str | None = Field(
        None, description="'at_least', 'less_than', or null if the count was exact")
    company_rating: float | None = None
    company_reviews: int | None = None
    url: str | None = None
    # Three states: True closed, False open, None never checked. Never test
    # for truthiness -- an unchecked posting is not an open one.
    is_expired: bool | None = None
    expired_on: date | None = None


class Qualification(BaseModel):
    level: str
    field_of_study: str | None = None


class QualificationDegree(BaseModel):
    """One accepted degree, broken out individually rather than as
    Naukri's flattened field_of_study string -- e.g. 'B.Tech / B.E.' in
    the raw text becomes two separate entries here, one for B.Tech and
    one for B.E., each with its own specializations."""
    degree: str
    level: str = Field(description="'UG', 'PG', or 'Doctorate'")
    specializations: list[str] = []


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
    qualification_degrees: list[QualificationDegree] = []
    first_seen_date: date | None = None
    last_seen_date: date | None = None
    times_seen: int | None = None
    days_listed: int | None = None
    is_expired: bool | None = None
    expired_on: date | None = None
    last_checked_on: date | None = None


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


class DegreeCount(BaseModel):
    name: str
    level: str = Field(description="'UG', 'PG', or 'Doctorate'")
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
    # Checked directly against Naukri, unlike active_last_7_days which only
    # says a search surfaced the posting. never_checked must stay its own
    # number: it is not the same as open.
    still_open: int = 0
    closed: int = 0
    never_checked: int = 0
    last_liveness_check: date | None = None
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


class SkillChoice(BaseModel):
    """One set of skills employers treat as interchangeable — a posting
    carrying this set wants any ONE of them, not all."""
    skills: list[str]
    postings: int


class ExperienceFlexibility(BaseModel):
    """How willing postings in one experience band are to accept a substitute
    skill. `offering_a_choice` counts postings with at least one either/or set;
    the rest name every skill outright."""
    bucket: str
    postings: int
    offering_a_choice: int
    pct_offering_a_choice: float


class ClosureRate(BaseModel):
    """How fast postings in one group stop being listed.

    `per_100_posting_days` is the figure to rank on, not `pct_closed`.
    Exposure differs between groups -- a posting first seen two weeks ago has
    had twice as long to close as one seen last week -- so a raw percentage
    partly measures when we happened to scrape. Adjusting moved one department
    from second place to first when this was checked against real data.

    `closed` counts postings Naukri now redirects as expired. It never means
    "filled": withdrawn, cancelled and expired-unfilled look identical from
    outside, which is why nothing here is named hiring."""
    bucket: str
    postings: int
    closed: int
    pct_closed: float
    mean_exposure_days: float
    per_100_posting_days: float


class SkillFlexibility(BaseModel):
    """How negotiable a skill is. `required` counts postings that ask for
    it outright; `alternative` counts those that would equally accept
    something else instead. A high negotiable_pct means employers care
    about the capability more than this specific tool."""
    skill: str
    required: int
    alternative: int
    total: int
    negotiable_pct: float
    swaps: list[str] = Field(
        default=[],
        description="Skills seen offered in place of this one, most common first.")


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


class PendingLocation(BaseModel):
    """A location fragment that matched no city and needs a curated entry.

    Cities are the one reference table that cannot auto-register an unseen
    value: `cities.state` is NOT NULL and a bare fragment gives nothing to
    fill it with."""
    fragment: str
    postings: int


class ScrapeHealthReport(BaseModel):
    latest_run: ScrapeRunSummary | None = None
    warnings: list[FieldHealthWarning] = []
    recent_runs: list[ScrapeRunSummary] = []
    pending_locations: list[PendingLocation] = Field(
        default=[],
        description="Location fragments awaiting a CITY_ALIASES entry and a state.")
