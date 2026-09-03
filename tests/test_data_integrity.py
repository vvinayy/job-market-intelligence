"""Assertions about the data already in the database, not about the code.

The tests elsewhere pin what the collectors and cleaners *do*. This file
catches the other failure: rows written under a rule that has since been
replaced, which no code test can see because the code is now correct.

That is not hypothetical. hirist's `isMandatory` tags were briefly written
into preferred_skill_ids; the collector was fixed and
test_build_record_does_not_report_mandatory_as_preferred has pinned it since,
but 19 rows written before the fix sat there contradicting it for a day,
telling readers a required skill was optional.

Read-only, and skips cleanly when Postgres is not reachable -- same contract
as test_api.py, so offline runs of the pure-function tests are unaffected.
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
def rows():
    from api.database import fetch_all
    return fetch_all


def test_no_hirist_posting_claims_preferred_skills(rows):
    """hirist publishes no preferred signal, so no hirist row may carry one.

    `isMandatory` is not it -- mandatory is close to the opposite claim, and
    every one of those skills is already in skill_ids where it belongs. If
    hirist ever does publish a genuine preferred marker, this test is the
    right place to change deliberately.
    """
    offenders = rows("""
        SELECT c.job_id, c.source,
               cardinality(c.preferred_skill_ids) AS on_posting,
               cardinality(COALESCE(ps.preferred_skill_ids, '{}')) AS on_skills
          FROM cleaned_postings c
          LEFT JOIN posting_skills ps ON ps.job_id = c.job_id
         WHERE c.source = 'hirist'
           AND (cardinality(c.preferred_skill_ids) > 0
                OR cardinality(COALESCE(ps.preferred_skill_ids, '{}')) > 0)
         ORDER BY c.job_id
    """)
    assert offenders == [], (
        f"{len(offenders)} hirist posting(s) carry preferred skills. The "
        f"collector does not map any, so these were written by a superseded "
        f"rule: {[o['job_id'] for o in offenders][:10]}"
    )


def test_preferred_skills_agree_across_both_tables(rows):
    """The same fact is stored twice, so the two copies must not diverge.

    cleaned_postings is what production computes off directly; posting_skills
    is what the API reads. A repair that touched one and not the other would
    leave the star showing in the dashboard while every aggregate said
    otherwise.
    """
    divergent = rows("""
        SELECT c.job_id
          FROM cleaned_postings c
          JOIN posting_skills ps ON ps.job_id = c.job_id
         WHERE COALESCE(c.preferred_skill_ids, '{}') <> COALESCE(ps.preferred_skill_ids, '{}')
         ORDER BY c.job_id
    """)
    assert divergent == [], (
        f"{len(divergent)} posting(s) disagree between "
        f"cleaned_postings.preferred_skill_ids and posting_skills: "
        f"{[d['job_id'] for d in divergent][:10]}"
    )


def test_every_posting_has_a_recognised_source(rows):
    """'other' is a legitimate value, but a board we actually collect from
    landing there means source_from_url() has not been taught about it."""
    stray = rows("""
        SELECT source, COUNT(*)::int AS postings
          FROM cleaned_postings
         WHERE source NOT IN ('naukri', 'hirist')
         GROUP BY source
    """)
    assert stray == [], f"postings with an unexpected source: {stray}"
