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
    at = AppTest.from_file(str(ROOT / "Home.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_market_page_loads_without_exception():
    at = AppTest.from_file(str(ROOT / "pages" / "2_Market.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_jobs_page_loads_without_exception():
    at = AppTest.from_file(str(ROOT / "pages" / "4_Jobs.py"), default_timeout=30)
    at.run()
    assert not at.exception
