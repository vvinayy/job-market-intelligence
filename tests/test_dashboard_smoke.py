"""Dashboard smoke tests via Streamlit's AppTest -- catches a page
raising an exception on load (e.g. a renamed API field breaking a
column reference) without needing a browser.

dash_common talks to the API over real HTTP, not through the FastAPI
TestClient used in test_api.py, so these need an actual server process
listening at API_BASE_URL (default http://localhost:8000) -- the same
precondition as the manual "run uvicorn, then open the dashboard"
verification this project has always used. Skips cleanly if nothing is
listening there rather than failing.
"""

import os
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest


API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
ROOT = Path(__file__).parent.parent


def _api_reachable() -> bool:
    try:
        return requests.get(f"{API_BASE}/health", timeout=3).ok
    except requests.exceptions.RequestException:
        return False


pytestmark = pytest.mark.skipif(not _api_reachable(), reason=f"API not reachable at {API_BASE}")


def test_home_page_loads_without_exception():
    at = AppTest.from_file(str(ROOT / "frontend" / "Home.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_market_page_loads_without_exception():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "2_Market.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_jobs_page_loads_without_exception():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "5_Jobs.py"), default_timeout=30)
    at.run()
    assert not at.exception


@pytest.mark.parametrize("page", ["1_Skills.py", "3_Trends.py", "4_Composition.py"])
def test_other_pages_load_without_exception(page):
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / page), default_timeout=30)
    at.run()
    assert not at.exception


def test_every_page_says_how_fresh_the_data_is():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "4_Composition.py"), default_timeout=30)
    at.run()
    shown = [c.value for c in at.caption] + [w.value for w in at.warning]
    assert any("Data last collected" in s for s in shown)


def test_jobs_filters_are_restored_from_a_shared_link():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "5_Jobs.py"), default_timeout=30)
    at.query_params["skill"] = ["Python", "Not A Real Skill"]
    at.query_params["exp"] = "2-8"
    at.query_params["status"] = "Still open"
    at.run()
    assert not at.exception
    # The unknown skill is dropped rather than crashing the multiselect.
    assert at.multiselect(key="jobs_skill").value == ["Python"]
    assert tuple(at.slider(key="jobs_exp").value) == (2, 8)
    assert at.radio(key="jobs_status").value == "Still open"
    # And written back, so the address bar reflects what is on screen.
    assert at.query_params["skill"] == ["Python"]


def test_a_malformed_link_is_ignored_not_fatal():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "5_Jobs.py"), default_timeout=30)
    at.query_params["exp"] = "junk"
    at.query_params["per_page"] = "7"
    at.run()
    assert not at.exception
    assert tuple(at.slider(key="jobs_exp").value) == (0, 20)
    assert at.select_slider(key="jobs_page_size").value == 25


def test_jobs_csv_holds_every_match_not_just_the_visible_page():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "5_Jobs.py"), default_timeout=60)
    at.run()
    button = next(b for b in at.button if b.label.startswith("Prepare CSV"))
    total = int(button.label.split()[4])   # "Prepare CSV of all N matching postings"
    assert total > 200, "needs more than one API page to prove the loop"
    button.click().run()
    assert not at.exception
    csv = at.session_state["jobs_csv"]
    assert len(csv) == total
    assert csv["job_id"].is_unique
    assert not csv["skills"].map(lambda v: isinstance(v, list)).any()


def test_one_skill_tab_opens_on_the_linked_skill():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "1_Skills.py"), default_timeout=30)
    at.query_params["skill"] = "Docker"
    at.run()
    assert not at.exception
    assert at.selectbox(key="one_skill").value == "Docker"
    at.radio(key="one_skill_scope").set_value("Open only").run()
    assert not at.exception


def test_market_over_time_tab_handles_every_breakdown():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "2_Market.py"), default_timeout=60)
    at.run()
    for by in ("Experience level", "City", "Company", "Job board", "Role"):
        at.radio(key="arr_by").set_value(by).run()
        assert not at.exception, by
    at.radio(key="arr_period").set_value("Days").run()
    at.radio(key="arr_measure").set_value("Share").run()
    assert not at.exception


def test_trends_skills_are_restored_from_a_shared_link():
    at = AppTest.from_file(str(ROOT / "frontend" / "pages" / "3_Trends.py"), default_timeout=30)
    at.query_params["skill"] = ["Python", "SQL"]
    at.run()
    assert not at.exception
    assert at.multiselect(key="trend_skills").value == ["Python", "SQL"]
