"""Unit tests for naukri_collector.py's pure parsing helpers -- the parts
that don't need a browser or a database. parse_applicant_count is the
regression target for a real bug: the old code extracted the digits from
'Less than 10' the same way as '100+', which is backwards (a ceiling
stored identically to a floor)."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import naukri_collector as nc
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


# ---------------------------------------------------------------------
# Applicant count -- three real formats confirmed by sampling Naukri
# ---------------------------------------------------------------------
def test_applicant_count_plain_number():
    assert nc.parse_applicant_count("44 Applicants") == (44, None)


def test_applicant_count_at_least():
    assert nc.parse_applicant_count("100+ Applicants") == (100, "at_least")


def test_applicant_count_less_than_is_a_ceiling_not_a_floor():
    assert nc.parse_applicant_count("Less than 10 Applicants") == (10, "less_than")


def test_applicant_count_missing():
    assert nc.parse_applicant_count(None) == (None, None)
    assert nc.parse_applicant_count("") == (None, None)


def test_applicant_count_no_digits():
    assert nc.parse_applicant_count("Applicants") == (None, None)


# ---------------------------------------------------------------------
# K/M shorthand expansion (company review counts)
# ---------------------------------------------------------------------
def test_parse_count_with_suffix_thousands():
    assert nc.parse_count_with_suffix("50.5K Reviews") == 50500


def test_parse_count_with_suffix_millions():
    assert nc.parse_count_with_suffix("1.2M") == 1_200_000


def test_parse_count_with_suffix_plain_number():
    assert nc.parse_count_with_suffix("850") == 850


def test_parse_count_with_suffix_none():
    assert nc.parse_count_with_suffix(None) is None
    assert nc.parse_count_with_suffix("") is None


# ---------------------------------------------------------------------
# Posted-date parsing -- "30+ days ago" must stay None, not a guess
# ---------------------------------------------------------------------
def test_posted_date_today_variants():
    assert nc.parse_posted_date("Today") == date.today()
    assert nc.parse_posted_date("Just now") == date.today()


def test_posted_date_n_days_ago():
    assert nc.parse_posted_date("3 days ago") == date.today() - timedelta(days=3)


def test_posted_date_plus_is_unknown():
    assert nc.parse_posted_date("30+ days ago") is None


def test_posted_date_weeks_ago_is_unknown():
    """Deliberately unhandled, not a gap to close. 'N days ago' has at
    most a day of slop; 'N weeks ago' has up to seven -- '2 weeks ago'
    could be day 8 or day 20, and there is no way to tell which from the
    text alone. Computing today - 14 would invent a specific day the
    source never gave us, the same mistake normalize_working_type() made
    once already. 304 live postings sit at posted_date IS NULL for this
    reason (2026-09-18); that is the honest count, not a bug."""
    assert nc.parse_posted_date("1 week ago") is None
    assert nc.parse_posted_date("2 weeks ago") is None
    assert nc.parse_posted_date("3+ weeks ago") is None


def test_posted_date_missing():
    assert nc.parse_posted_date(None) is None
    assert nc.parse_posted_date("") is None


def test_posted_date_unrecognised_phrasing():
    assert nc.parse_posted_date("sometime, probably") is None


# ---------------------------------------------------------------------
# Field-health input: found-count tallying
# ---------------------------------------------------------------------
def test_compute_field_found_counts_skips_sentinels_and_empties():
    records = [
        {"title": "Dev", "company": "not found", "skills": [], "openings": 2},
        {"title": "Dev", "company": "Acme", "skills": ["Python"], "openings": None},
    ]
    counts = nc.compute_field_found_counts(records)
    assert counts["title"] == 2
    assert counts["company"] == 1  # one record's company was "not found"
    assert counts["skills"] == 1  # one record's skills list was empty
    assert counts["openings"] == 1  # one record's openings was None


def test_compute_field_found_counts_empty_input():
    assert nc.compute_field_found_counts([]) == {}


# ---------------------------------------------------------------------
# Regression: a slow detail page must cost one posting, not the search.
# Unguarded until 2026-09-23 -- page.goto() raising here propagated out of
# main() and took postings 17-20 of a 20-posting run down with it, with no
# scrape_runs row left behind. Same contract as the wait_for_selector
# timeout three lines below it in scrape_job_detail(), which already
# returns None instead of raising.
# ---------------------------------------------------------------------
def test_scrape_job_detail_returns_none_on_navigation_timeout():
    page = MagicMock()
    page.goto.side_effect = PlaywrightTimeoutError("Page.goto: Timeout 45000ms exceeded.")

    record = nc.scrape_job_detail(page, "https://www.naukri.com/job-listings-x")

    assert record is None
