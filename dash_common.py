"""
Shared helpers for the dashboard pages — API client version.

Previously this file ran SQL directly against Postgres. It now calls the
FastAPI backend over HTTP instead, and knows nothing about the database
schema — only about JSON shapes the API returns. That's the actual point
of this rewrite: change a table or column, and only api/routers/*.py
needs to change. Every page below keeps working unmodified.
"""

import os
import requests
import pandas as pd
import streamlit as st


# Where the API lives. Same pattern as DATABASE_URL: local default,
# overridable via environment variable or Streamlit secrets when deployed.
#
# 127.0.0.1, not localhost: on Windows "localhost" resolves to ::1 first, and
# with the API listening on IPv4 only every call waits out a ~2s connect
# timeout before falling back. Measured 2048 ms/call against 7 ms. A page
# firing 11 calls took 22 seconds purely on that.
_DEFAULT_API = "http://127.0.0.1:8000"
try:
    API_BASE = st.secrets.get("API_BASE_URL", os.environ.get("API_BASE_URL", _DEFAULT_API))
except Exception:
    API_BASE = os.environ.get("API_BASE_URL", _DEFAULT_API)

# One Session for the process, so the TCP connection is opened once and reused
# rather than reopened per call. Worth ~2s per call on its own when the host
# resolves slowly, and a few ms even when it doesn't.
_SESSION = requests.Session()

# Data changes once a day, after the scheduled scrape — a 5 minute TTL meant
# re-fetching everything roughly 288 times more often than it can change.
# Streamlit's menu has "Clear cache" if you need it sooner.
CACHE_TTL = 1800


PALETTE = ["#00b4c8", "#c8d400", "#6b7280", "#0891a5", "#9aa300", "#4b5563"]
SCALE = ["#6b7280", "#00b4c8", "#c8d400"]
TRANSPARENT = dict(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")

# Static, real coordinates for every city in the `cities` reference
# table — a display-only lookup, not scraped or derived data, so it
# lives here rather than in the schema. Matches CITY_ALIASES in
# cleaning.py in spirit: hardcoded reference facts, not a guess.
CITY_COORDINATES = {
    "Bengaluru": (12.9716, 77.5946), "Hyderabad": (17.3850, 78.4867),
    "Secunderabad": (17.4399, 78.4983), "Nizamabad": (18.6725, 78.0941),
    "Warangal": (17.9689, 79.5941), "Pune": (18.5204, 73.8567),
    "Mumbai": (19.0760, 72.8777), "Chennai": (13.0827, 80.2707),
    "Coimbatore": (11.0168, 76.9558), "Delhi": (28.7041, 77.1025),
    "Delhi / NCR": (28.4595, 77.0266), "Gurugram": (28.4595, 77.0266),
    "Faridabad": (28.4089, 77.3178), "Noida": (28.5355, 77.3910),
    "Greater Noida": (28.4744, 77.5040), "Ghaziabad": (28.6692, 77.4538),
    "Kolkata": (22.5726, 88.3639), "Ahmedabad": (23.0225, 72.5714),
    "Kochi": (9.9312, 76.2673), "Thiruvananthapuram": (8.5241, 76.9366),
    "Jaipur": (26.9124, 75.7873), "Indore": (22.7196, 75.8577),
    "Chandigarh": (30.7333, 76.7794), "Bhubaneswar": (20.2961, 85.8245),
    "Visakhapatnam": (17.6868, 83.2185),
}


@st.cache_data(ttl=CACHE_TTL)
def api_get(path: str, params: tuple = ()) -> list[dict]:
    """Call one API endpoint, return its JSON as a list of dicts.

    params is a tuple of (key, value) pairs rather than a dict, because
    dicts aren't hashable and st.cache_data needs hashable arguments to
    know when it's seen a call before. Repeated values (e.g. several
    `skill=` filters) are passed as repeated tuple entries.
    """
    try:
        response = _SESSION.get(f"{API_BASE}{path}", params=list(params), timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error(
            f"Can't reach the API at {API_BASE}. Is it running? "
            "Locally: `uvicorn api.main:app --reload` in a separate terminal."
        )
        st.stop()
    except requests.exceptions.HTTPError as e:
        st.error(f"API returned an error for {path}: {e}")
        return []


def df(path: str, params: tuple = ()) -> pd.DataFrame:
    """api_get, wrapped in a DataFrame — most pages want tabular data."""
    return pd.DataFrame(api_get(path, params))


@st.cache_data(ttl=CACHE_TTL)
def one(path: str, params: tuple = ()) -> dict:
    """For endpoints that return a single object, not a list — /analytics/summary
    and /trends/coverage are dicts, not arrays, so they skip the DataFrame step.

    Cached like api_get. It wasn't, so /analytics/summary re-fetched on every
    widget interaction on every page that shows a headline figure."""
    try:
        response = _SESSION.get(f"{API_BASE}{path}", params=list(params), timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Can't reach the API at {API_BASE}.")
        st.stop()
    except requests.exceptions.HTTPError as e:
        st.error(f"API returned an error for {path}: {e}")
        # {} not [] — every caller reads this via .get(), which needs a dict
        # to degrade safely instead of raising AttributeError on top of the
        # original failure.
        return {}


# ---------------------------------------------------------------------
# Named wrappers. Thin on purpose — each just shapes the parameters and
# hands back a DataFrame with the column names pages already expect,
# so page files needed minimal changes when this module switched from
# SQL to HTTP underneath them.
# ---------------------------------------------------------------------

def summary() -> dict:
    return one("/analytics/summary")


def scrape_health(lookback: int = 20) -> dict:
    return one("/analytics/scrape-health", (("lookback", lookback),))


def skill_demand(limit: int = 25, **filters) -> pd.DataFrame:
    params = [("limit", limit)] + _filter_params(filters)
    data = df("/analytics/skills", tuple(params))
    return data.rename(columns={"name": "skill"}) if not data.empty else data


def top_skills(limit: int = 60) -> list[str]:
    data = df("/reference/skills", (("limit", limit),))
    return data["skill"].tolist() if not data.empty else []


def co_occurrence(top_n: int = 20, min_together: int = 1) -> pd.DataFrame:
    return df("/analytics/co-occurrence",
              (("top_skills", top_n), ("min_together", min_together), ("limit", 500)))


def skill_choices(limit: int = 15, min_postings: int = 2) -> pd.DataFrame:
    """Sets of skills employers treat as interchangeable. Distinct from
    co_occurrence(): that shows skills wanted TOGETHER, this shows skills
    offered INSTEAD of each other."""
    return df("/analytics/skill-choices",
              (("limit", limit), ("min_postings", min_postings)))


def skill_flexibility(limit: int = 15, min_postings: int = 5) -> pd.DataFrame:
    return df("/analytics/skill-flexibility",
              (("limit", limit), ("min_postings", min_postings)))


def role_distribution(**filters) -> pd.DataFrame:
    data = df("/analytics/roles", tuple(_filter_params(filters)))
    return data.rename(columns={"bucket": "role"}) if not data.empty else data


def experience_distribution(**filters) -> pd.DataFrame:
    return df("/analytics/experience", tuple(_filter_params(filters)))


def seniority_distribution(**filters) -> pd.DataFrame:
    return df("/analytics/seniority", tuple(_filter_params(filters)))


def flexibility_by_experience(**filters) -> pd.DataFrame:
    """Share of postings in each experience band that offer an either/or skill
    rather than naming everything outright."""
    return df("/analytics/flexibility-by-experience", tuple(_filter_params(filters)))


def closures(dimension: str = "experience_band", min_postings: int = 15,
             **filters) -> pd.DataFrame:
    """How fast postings close, per group. Rank on per_100_posting_days --
    pct_closed compares groups watched for different lengths of time."""
    params = [("dimension", dimension), ("min_postings", min_postings)] + _filter_params(filters)
    return df("/analytics/closures", tuple(params))


def location_distribution(by: str = "city", limit: int = 30, **filters) -> pd.DataFrame:
    params = [("by", by), ("limit", limit)] + _filter_params(filters)
    return df("/analytics/locations", tuple(params))


def openings_distribution() -> pd.DataFrame:
    return df("/analytics/openings")


def qualification_distribution() -> pd.DataFrame:
    return df("/analytics/qualifications")


def skill_category_mix(**filters) -> pd.DataFrame:
    return df("/analytics/skill-categories", tuple(_filter_params(filters)))


def skill_suggestions(known: list[str], limit: int = 12) -> tuple[pd.DataFrame, int]:
    """Returns (suggestions, base_count) — base_count is how many postings
    matched at least one known skill, needed to show 'based on N postings'."""
    params = [("skill", s) for s in known] + [("limit", limit)]
    data = df("/analytics/skill-suggestions", tuple(params))
    base = int(data["postings"].sum() / (data["share_pct"].iloc[0] / 100)) if not data.empty else 0
    return data, base


def companies(limit: int = 20, min_postings: int = 1) -> pd.DataFrame:
    data = df("/reference/companies", (("limit", limit), ("min_postings", min_postings)))
    return data.rename(columns={"name": "company"}) if not data.empty else data


def cities_reference(with_postings_only: bool = True) -> pd.DataFrame:
    return df("/reference/cities", (("with_postings_only", with_postings_only),))


def states_reference() -> pd.DataFrame:
    return df("/reference/states")


def roles() -> list[str]:
    data = df("/reference/roles")
    return data["name"].tolist() if not data.empty else []


def industry_types() -> pd.DataFrame:
    """Naukri's Industry Type tag, e.g. "IT Services & Consulting"."""
    return df("/reference/industry-types")


def departments() -> pd.DataFrame:
    """Naukri's Department tag, e.g. "Engineering - Software & QA"."""
    return df("/reference/departments")


def education_degrees() -> pd.DataFrame:
    """Accepted degrees, one row each. Naukri's "B.Tech / B.E." is two rows."""
    return df("/reference/education-degrees")


def education_specializations() -> pd.DataFrame:
    """Accepted fields of study. "Any Specialization" is a real answer here,
    not a missing value."""
    return df("/reference/education-specializations")


def working_types() -> pd.DataFrame:
    return df("/reference/working-types")


def employment_types() -> pd.DataFrame:
    return df("/reference/employment-types")


def contract_types() -> pd.DataFrame:
    return df("/reference/contract-types")


def trends_coverage() -> dict:
    return one("/trends/coverage")


def skill_series(skills: list[str]) -> pd.DataFrame:
    if not skills:
        return pd.DataFrame()
    return df("/trends/skills", tuple(("skill", s) for s in skills))


def movers(limit: int = 25, skills: list[str] | None = None) -> tuple[pd.DataFrame, str]:
    """Returns (movers, comparison_mode) — the API picks rolling_7d once
    enough history exists, previous_day otherwise, and reports which.
    Pass skills to see specific ones instead of the biggest movers overall."""
    params = [("comparison", "auto"), ("limit", limit)]
    if skills:
        params += [("skill", s) for s in skills]
    data = df("/trends/movers", tuple(params))
    mode = data["comparison"].iloc[0] if not data.empty else None
    return data, mode


def new_skills(limit: int = 40, since: str | None = None) -> pd.DataFrame:
    """Pass `since` when you want a COUNT of what is new rather than a sample
    of it. Filtering a limited page client-side reports the limit, not the
    count -- the weekly digest said "40 skills" for weeks when the real figure
    was 121."""
    params = [("limit", limit)]
    if since:
        params.append(("since", since))
    return df("/trends/new-skills", tuple(params))


def search_postings(page: int = 1, page_size: int = 25,
                     sort_by: str = "posted_date", order: str = "desc",
                     **filters) -> tuple[pd.DataFrame, dict]:
    """Calls /postings, which returns a page object rather than a bare
    list, so it can't reuse df() like the other wrappers. Returns
    (items, meta) — meta carries total/page/page_size/pages for a
    pagination control."""
    params = [("page", page), ("page_size", page_size),
              ("sort_by", sort_by), ("order", order)] + _filter_params(filters)
    data = one("/postings", tuple(params))
    items = pd.DataFrame(data.get("items", []))
    meta = {k: data.get(k) for k in ("total", "page", "page_size", "pages")}
    return items, meta


def posting_detail(job_id: int) -> dict:
    return one(f"/postings/{job_id}")


def _filter_params(filters: dict) -> list[tuple]:
    """Flatten a dict of optional filters into (key, value) tuples,
    expanding lists into repeated keys and dropping anything unset —
    matches how FastAPI expects repeated query parameters."""
    params = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            params.extend((key, v) for v in value)
        else:
            params.append((key, value))
    return params


def sampling_note():
    st.caption(
        "Figures describe postings collected from a fixed set of Naukri searches "
        "and cities, not the Indian IT market as a whole."
    )


# ---------------------------------------------------------------------
# Prefetch — warm every shared endpoint once, so moving between pages is
# instant instead of paying a round trip per chart on arrival.
# ---------------------------------------------------------------------
def prefetch() -> None:
    """Populate the cache for the endpoints more than one page reads.

    Cheap because it runs through the same cached wrappers: on a warm cache
    this is a no-op, and cold it is one pass over ~15 endpoints on a reused
    connection. Page-specific, parameterised calls (a posting search, a skill
    series for a chosen skill) are deliberately not prefetched — they depend
    on user input and would be cache misses anyway.
    """
    if st.session_state.get("_prefetched"):
        return
    for call in (summary, trends_coverage, roles, working_types, employment_types,
                 contract_types, skill_demand, role_distribution,
                 experience_distribution, location_distribution,
                 qualification_distribution, openings_distribution,
                 skill_category_mix, cities_reference):
        try:
            call()
        except Exception:
            # A single failing endpoint must not stop the dashboard opening;
            # the page that actually needs it will surface the error itself.
            pass
    st.session_state["_prefetched"] = True