"""Unit tests for naukri_collector.py's pure parsing helpers -- the parts
that don't need a browser or a database. parse_applicant_count is the
regression target for a real bug: the old code extracted the digits from
'Less than 10' the same way as '100+', which is backwards (a ceiling
stored identically to a floor)."""

from datetime import date, timedelta

import naukri_collector as nc


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
