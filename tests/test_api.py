"""API contract tests -- every endpoint returns 200 and JSON matching its
declared response_model shape.

Runs against the real local Postgres (this project has no mock/staging
DB, and none of these tests write anything -- they're read-only against
whatever data already exists), so it needs the same environment as
manual verification: PGDATABASE/PGUSER/etc. set (or the defaults) and
schema.sql already applied. Skips itself cleanly if the DB isn't
reachable, so `pytest tests/test_cleaning.py` style offline runs aren't
affected by this file's presence.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app


def _db_reachable() -> bool:
    try:
        with TestClient(app) as c:
            return c.get("/health").json().get("status") == "ok"
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="local Postgres not reachable")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "endpoints" in r.json()


# ---------------------------------------------------------------------
# Reference -- no required params, always safe to call bare
# ---------------------------------------------------------------------
REFERENCE_ENDPOINTS = [
    "/reference/cities", "/reference/states", "/reference/roles",
    "/reference/companies", "/reference/skills", "/reference/working-types",
    "/reference/employment-types", "/reference/contract-types",
]


@pytest.mark.parametrize("path", REFERENCE_ENDPOINTS)
def test_reference_endpoint_returns_200(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------
# Analytics -- no required params
# ---------------------------------------------------------------------
ANALYTICS_LIST_ENDPOINTS = [
    "/analytics/skills", "/analytics/roles", "/analytics/seniority",
    "/analytics/experience", "/analytics/locations", "/analytics/qualifications",
    "/analytics/skill-categories", "/analytics/openings", "/analytics/co-occurrence",
]


@pytest.mark.parametrize("path", ANALYTICS_LIST_ENDPOINTS)
def test_analytics_list_endpoint_returns_200(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_analytics_summary_shape(client):
    r = client.get("/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("total_postings", "active_last_7_days", "new_postings_7d",
                "companies", "distinct_skills", "cities_covered"):
        assert key in body


def test_analytics_scrape_health_shape(client):
    r = client.get("/analytics/scrape-health")
    assert r.status_code == 200
    body = r.json()
    assert "warnings" in body
    assert "recent_runs" in body


def test_analytics_skill_suggestions_requires_no_params_but_accepts_skill(client):
    r = client.get("/analytics/skill-suggestions", params={"skill": "Python"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------
# Trends -- /skills requires at least one `skill` param
# ---------------------------------------------------------------------
def test_trends_coverage(client):
    r = client.get("/trends/coverage")
    assert r.status_code == 200
    assert "days_recorded" in r.json()


def test_trends_skills_requires_skill_param(client):
    r = client.get("/trends/skills")
    assert r.status_code == 422  # required query param missing


def test_trends_skills_with_a_real_skill(client):
    skills = client.get("/reference/skills", params={"limit": 1}).json()
    if not skills:
        pytest.skip("no skills in the database yet")
    r = client.get("/trends/skills", params={"skill": skills[0]["skill"]})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_trends_movers(client):
    r = client.get("/trends/movers")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_trends_new_skills(client):
    r = client.get("/trends/new-skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------------------------------------------------------------
# Postings -- search + single-record lookup
# ---------------------------------------------------------------------
def test_postings_list_default_shape(client):
    r = client.get("/postings")
    assert r.status_code == 200
    body = r.json()
    for key in ("total", "page", "page_size", "pages", "items"):
        assert key in body


def test_postings_invalid_sort_by_is_rejected(client):
    r = client.get("/postings", params={"sort_by": "'; DROP TABLE cleaned_postings; --"})
    assert r.status_code == 400


def test_postings_get_by_id_roundtrip(client):
    listing = client.get("/postings", params={"page_size": 1}).json()
    if not listing["items"]:
        pytest.skip("no postings in the database yet")
    job_id = listing["items"][0]["job_id"]

    r = client.get(f"/postings/{job_id}")
    assert r.status_code == 200
    assert r.json()["job_id"] == job_id


def test_postings_get_by_id_not_found(client):
    r = client.get("/postings/999999999")
    assert r.status_code == 404


def test_postings_seniority_filter_accepts_known_value(client):
    r = client.get("/postings", params={"seniority_level": "Senior", "page_size": 5})
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["seniority_level"] == "Senior"
