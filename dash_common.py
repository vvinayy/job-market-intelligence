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
try:
    API_BASE = st.secrets.get("API_BASE_URL", os.environ.get("API_BASE_URL", "http://localhost:8000"))
except Exception:
    API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")


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


@st.cache_data(ttl=300)
def api_get(path: str, params: tuple = ()) -> list[dict]:
    """Call one API endpoint, return its JSON as a list of dicts.

    params is a tuple of (key, value) pairs rather than a dict, because
    dicts aren't hashable and st.cache_data needs hashable arguments to
    know when it's seen a call before. Repeated values (e.g. several
    `skill=` filters) are passed as repeated tuple entries.
    """
    try:
        response = requests.get(f"{API_BASE}{path}", params=list(params), timeout=15)
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


def one(path: str, params: tuple = ()) -> dict:
    """For endpoints that return a single object, not a list — /analytics/summary
    and /trends/coverage are dicts, not arrays, so they skip the DataFrame step."""
    try:
        response = requests.get(f"{API_BASE}{path}", params=list(params), timeout=15)
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


def role_distribution(**filters) -> pd.DataFrame:
    data = df("/analytics/roles", tuple(_filter_params(filters)))
    return data.rename(columns={"bucket": "role"}) if not data.empty else data


def experience_distribution(**filters) -> pd.DataFrame:
    return df("/analytics/experience", tuple(_filter_params(filters)))


def seniority_distribution(**filters) -> pd.DataFrame:
    return df("/analytics/seniority", tuple(_filter_params(filters)))


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


def new_skills(limit: int = 40) -> pd.DataFrame:
    return df("/trends/new-skills", (("limit", limit),))


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
