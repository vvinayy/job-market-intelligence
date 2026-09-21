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
               cardinality(COALESCE(ps.preferred_skill_ids, '{}')) AS on_skills
          FROM cleaned_postings c
          LEFT JOIN posting_skills ps ON ps.job_id = c.job_id
         WHERE c.source = 'hirist'
           AND cardinality(COALESCE(ps.preferred_skill_ids, '{}')) > 0
         ORDER BY c.job_id
    """)
    assert offenders == [], (
        f"{len(offenders)} hirist posting(s) carry preferred skills. The "
        f"collector does not map any, so these were written by a superseded "
        f"rule: {[o['job_id'] for o in offenders][:10]}"
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


def test_demand_view_never_counts_a_skill_twice_for_one_posting(rows):
    """The view is the only place the outright/alternative duplicate is
    removed, so this pins the property the endpoints now rely on.

    skill_ids and skill_groups are *meant* to be disjoint, but nothing
    enforces it and four rows are not: the 2026-09-16 merge migration
    repointed 'Iac Terraform' into Terraform inside skill_ids on postings
    that already offered Terraform in a choice group, and it verified only
    that no id appeared twice WITHIN skill_ids. Concatenating then yields
    the skill twice, which inflated every COUNT(*) read path -- Terraform
    150 postings against a true 148.

    Deduplicating here rather than repairing the rows is deliberate: such
    a posting really does demand the skill outright AND list it as an
    alternative, so both copies are true and deleting either loses a fact.
    """
    dupes = rows("""
        SELECT job_id, skill_id, COUNT(*)::int AS n
          FROM posting_skill_demand
         GROUP BY job_id, skill_id
        HAVING COUNT(*) > 1
    """)
    assert dupes == [], f"posting_skill_demand yielded a skill twice: {dupes}"


def test_demand_view_loses_no_posting_and_invents_none(rows):
    """Deduplicating must not drop a posting. Every posting carrying any
    skill -- outright or only as an alternative -- must appear."""
    mismatch = rows("""
        SELECT (SELECT COUNT(DISTINCT job_id)::int FROM posting_skill_demand) AS in_view,
               (SELECT COUNT(*)::int FROM posting_skills
                 WHERE skill_ids <> '{}' OR skill_groups <> '[]'::jsonb) AS with_skills
    """)[0]
    assert mismatch["in_view"] == mismatch["with_skills"], (
        f"view covers {mismatch['in_view']} postings, "
        f"{mismatch['with_skills']} have skills")


def test_skills_endpoint_agrees_with_the_skill_filter(rows):
    """The 2026-09-17 bug in one assertion: /analytics/skills counted
    skill_ids alone and said AWS appeared in 295 postings while
    /postings?skill=AWS returned 419. Both now resolve demand the same
    way, so for any skill the two must agree exactly."""
    disagreements = rows("""
        WITH from_view AS (
            SELECT sk.skill_name, COUNT(*)::int AS n
              FROM posting_skill_demand d
              JOIN skills sk ON sk.skill_id = d.skill_id
             GROUP BY 1),
        from_filter AS (
            SELECT sk.skill_name, COUNT(DISTINCT ps.job_id)::int AS n
              FROM posting_skills ps
              JOIN skills sk
                ON ARRAY[sk.skill_id] && ps.skill_ids
                OR ARRAY[sk.skill_id] && skill_group_ids(ps.skill_groups)
             GROUP BY 1)
        SELECT v.skill_name, v.n AS view_count, f.n AS filter_count
          FROM from_view v JOIN from_filter f USING (skill_name)
         WHERE v.n <> f.n
         ORDER BY abs(v.n - f.n) DESC LIMIT 10
    """)
    assert disagreements == [], (
        f"demand disagrees between the view and the ?skill= filter: {disagreements}")
