-- ---------------------------------------------------------------------
-- skill_renames: make a renamed skill's own history findable again.
--
-- skill_daily_counts stores the skill NAME, not an id. When a spelling
-- enters SKILL_ALIASES, normalize_skill() starts emitting the canonical
-- and the series simply stops under the old name and restarts under the
-- new one. Nothing errors; the old rows just sit under a label nothing
-- looks up. Measured 2026-09-17: 145 such names holding 1,586 mentions.
--
-- The visible damage is /trends/new-skills. skill_first_appearances read
-- PowerShell as first seen 2026-08-20, BigQuery, EC2, SQL Server,
-- scikit-learn and Shell scripting likewise -- every one of them present
-- since 2026-08-07 under a title-case spelling. A rename was being
-- reported to the reader as a new skill appearing in the market.
--
-- WHY A TABLE AND NOT A BACKFILL. Rewriting the old names in
-- skill_daily_counts would make the series continuous and destroy the
-- evidence that the detector ever emitted them, leaving no explanation
-- for why a series changes shape mid-August. This is the same shape as
-- skill_daily_corrections: the raw observation is never touched, the
-- adjustment lives in its own table, and a view applies it. The
-- distinction that makes it legitimate is that a correction fixes a
-- COUNT that was wrong, while this fixes a LABEL -- the count was right
-- both before and after, so nothing here revises an observation.
--
-- ONLY 27 OF THE 145 ARE HERE, AND THE OMISSION IS THE POINT.
-- posting_count is COUNT(DISTINCT job_id), so two names can only be added
-- when no posting could have been counted under both -- which is provable
-- only when they share no snapshot_date. Grouping by canonical and testing
-- every member against every other:
--
--     27 names, 550 mentions   disjoint dates      -> stitchable, below
--     77 names, 1,412 mentions share a date        -> NOT recoverable
--     41 names, 124 mentions   no canonical exists -> nothing to map to
--
-- The 77 ran in parallel, not in sequence: Terraform and "Iac Terraform"
-- were both being counted daily, and 22 of the 23 postings holding the
-- latter also held the former. Adding those two series would report 83
-- postings where roughly 61 existed. The true union is not derivable from
-- two counts, so inventing it is worse than leaving the split visible.
-- "Bash" and "Shell Scripting" are why the test is group-wise rather than
-- pairwise against the canonical: neither overlaps "Shell scripting", but
-- they overlap each other.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-17-skill-renames.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TABLE IF NOT EXISTS skill_renames (
    old_name       TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    -- Recorded so a reader can tell a rename from a correction without
    -- reading this file.
    noted_on       DATE NOT NULL DEFAULT CURRENT_DATE,
    CHECK (old_name <> canonical_name)
);

COMMENT ON TABLE skill_renames IS
    'Old skill spellings mapped to the canonical name, so a rename does not '
    'split its own history. Only pairs whose snapshot dates are disjoint may '
    'be listed: posting_count is a distinct-posting count, so overlapping '
    'series cannot be summed without double-counting.';

INSERT INTO skill_renames (old_name, canonical_name) VALUES
    ('.Net'                     , '.NET'),
    ('.Net Core'                , '.NET Core'),
    ('Agentic Ai'               , 'Agentic AI'),
    ('Asp.Net'                  , 'ASP.NET'),
    ('Bigquery'                 , 'BigQuery'),
    ('Cloud Watch'              , 'Cloudwatch'),
    ('Ec2'                      , 'EC2'),
    ('Event Bridge'             , 'Eventbridge'),
    ('Golang'                   , 'Go'),
    ('Grpc'                     , 'gRPC'),
    ('Iics'                     , 'IICS'),
    ('Java Full Stack Developer', 'Java Fullstack Developer'),
    ('Jpa'                      , 'JPA'),
    ('Jquery'                   , 'jQuery'),
    ('Junit'                    , 'JUnit'),
    ('Langchain'                , 'LangChain'),
    ('Mvc'                      , 'MVC'),
    ('Numpy'                    , 'NumPy'),
    ('Nunit'                    , 'NUnit'),
    ('Opensearch'               , 'OpenSearch'),
    ('Php'                      , 'PHP'),
    ('Pl-Sql'                   , 'PL/SQL'),
    ('Powershell'               , 'PowerShell'),
    ('Pytest'                   , 'pytest'),
    ('Scikit-Learn'             , 'scikit-learn'),
    ('Sql Server'               , 'SQL Server'),
    ('Web Api'                  , 'Web API')
ON CONFLICT (old_name) DO NOTHING;

-- Refuse to commit anything that would double-count. Checks the whole
-- group, canonical included, not just each pair against the canonical.
DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(DISTINCT canonical, ', ') INTO bad FROM (
        SELECT COALESCE(r1.canonical_name, a.skill) AS canonical
          FROM skill_daily_counts a
          JOIN skill_daily_counts b
            ON a.snapshot_date = b.snapshot_date
           AND a.skill <> b.skill
          LEFT JOIN skill_renames r1 ON r1.old_name = a.skill
          LEFT JOIN skill_renames r2 ON r2.old_name = b.skill
         WHERE COALESCE(r1.canonical_name, a.skill)
             = COALESCE(r2.canonical_name, b.skill)
    ) x;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'these canonicals would double-count on a shared date: %', bad;
    END IF;
END $$;

-- The seam. Every trend view reads this one, so resolving the name here
-- reaches skill_daily_counts_corrected, both delta views and
-- skill_first_appearances without touching any of them.
--
-- Corrections still join on the RAW name: a correction was written against
-- the spelling observed that day, and that is the row it belongs to.
-- snapshot_coverage deliberately keeps reading the raw table -- it answers
-- "which days were recorded", which a rename does not change.
CREATE OR REPLACE VIEW skill_daily_counts_by_source AS
SELECT
    s.snapshot_date,
    COALESCE(r.canonical_name, s.skill)    AS skill,
    s.source,
    s.posting_count + COALESCE(c.delta, 0) AS posting_count,
    s.posting_count                        AS raw_count,
    COALESCE(c.delta, 0)                   AS correction
FROM skill_daily_counts s
LEFT JOIN skill_renames r ON r.old_name = s.skill
LEFT JOIN (
    SELECT snapshot_date, skill, source, SUM(delta) AS delta
    FROM skill_daily_corrections
    GROUP BY snapshot_date, skill, source
) c ON c.snapshot_date = s.snapshot_date
   AND c.skill = s.skill
   AND c.source = s.source
WHERE s.posting_count + COALESCE(c.delta, 0) > 0;

COMMIT;

SELECT skill, first_seen, days_present
  FROM skill_first_appearances
 WHERE skill IN ('PowerShell','BigQuery','EC2','.NET','SQL Server',
                 'scikit-learn','NumPy','LangChain','JPA')
 ORDER BY skill;
