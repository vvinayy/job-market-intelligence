-- ---------------------------------------------------------------------
-- Give 39 placeable technologies the category they should always have had.
--
-- skills.category is written ONCE, by cleaning.categorize_skill() at the
-- moment a skill is first registered, and nothing refreshes it. An entry
-- added to SKILL_CATEGORIES later never reaches rows already there. This
-- file is that refresh, for the entries added to cleaning.py on 2026-09-16.
--
-- WHAT THIS IS NOT. It is not an attempt to categorise the ~1,400
-- uncategorised skills. NULL is not a backlog here -- it is the filter
-- /analytics/skill-categories uses to keep broad tags ("Agile", "Cloud",
-- "Communication Skills", "Java Fullstack") out of the mix chart, which the
-- endpoint and the dashboard caption both state outright. Categorising
-- those would invert the filter and make "Role" the largest slice of a
-- chart about technical composition. Only skills that are specific,
-- placeable technologies belong here.
--
-- Every name below was already being counted as demand and was missing
-- from the category chart for no reason other than absence from the dict.
-- Chosen from skills used by 5 or more postings on 2026-09-16 -- a
-- threshold, not a complete pass. Below that line sit 870 skills appearing
-- in exactly one posting, where the judgement cost is high and the effect
-- on any chart is a rounding error.
--
-- Run AFTER migrations/2026-09-16-merge-duplicate-skill-spellings.sql:
-- that one folds Iac Terraform, Snowflake Db, Django Framework, Data Build
-- Tool, Reacts Js and Datalake into names that are ALREADY categorised, so
-- running it first means they do not need an entry here at all.
--
-- Idempotent: the UPDATE is a no-op on a row that already matches, and
-- nothing here can clear a category -- verified by the guard below.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-16-backfill-skill-categories.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE _cats (skill_name TEXT PRIMARY KEY, category TEXT NOT NULL)
ON COMMIT DROP;

INSERT INTO _cats (skill_name, category) VALUES
    -- Products and tools. Unambiguous: each names a thing you install,
    -- call or deploy.
    ('Azure Devops',      'Cloud/DevOps'),
    ('Aws Devops',        'Cloud/DevOps'),
    ('Eks',               'Cloud/DevOps'),
    ('Iam',               'Cloud/DevOps'),
    ('Prometheus',        'Cloud/DevOps'),
    ('Github Actions',    'Cloud/DevOps'),
    ('Api Gateway',       'Cloud/DevOps'),
    ('Amazon Ec2',        'Cloud/DevOps'),
    ('Helm',              'Cloud/DevOps'),
    ('Amazon Cloudwatch', 'Cloud/DevOps'),
    ('Cloudwatch',        'Cloud/DevOps'),
    ('Cloudformation',    'Cloud/DevOps'),
    ('Openshift',         'Cloud/DevOps'),
    ('Bitbucket',         'Cloud/DevOps'),
    ('Aws Sagemaker',     'Data/ML'),
    ('Matplotlib',        'Data/ML'),
    ('Langgraph',         'Data/ML'),
    ('Vertex Ai',         'Data/ML'),
    ('Synapse Analytics', 'Data/ML'),
    ('Sqoop',             'Data/ML'),
    ('Bedrock',           'Data/ML'),
    ('Amazon Rds',        'Database'),
    ('Ssis',              'Database'),
    ('Maven',             'Backend'),
    ('Gradle',            'Backend'),
    ('J2ee',              'Backend'),
    ('Apex',              'Languages'),
    ('Bash Scripting',    'Languages'),

    -- Techniques rather than products, and therefore a judgement rather
    -- than an observation. Included because "Computer Vision" names a kind
    -- of work as specifically as "Kafka" names a tool, and the chart's
    -- question is what kind of work a role is made of. Kept as its own
    -- block so the judgement is visible and can be reversed without
    -- disturbing the list above.
    ('Computer Vision',                'Data/ML'),
    ('Statistics',                     'Data/ML'),
    ('Neural Networks',                'Data/ML'),
    ('Feature Engineering',            'Data/ML'),
    ('Predictive Analytics',           'Data/ML'),
    ('Clustering',                     'Data/ML'),
    ('Classification',                 'Data/ML'),
    ('Business Intelligence',          'Data/ML'),
    ('Retrieval Augmented Generation', 'Data/ML'),
    ('Stored Procedures',              'Database'),
    ('Rdbms',                          'Database');

-- Refuse to run against a category value the schema does not already use:
-- a typo here would create a silent one-skill category in the mix chart.
DO $$
DECLARE stray text;
BEGIN
    SELECT string_agg(DISTINCT c.category, ', ') INTO stray
      FROM _cats c
     WHERE c.category NOT IN (SELECT category FROM skills WHERE category IS NOT NULL);
    IF stray IS NOT NULL THEN
        RAISE EXCEPTION 'unknown category value(s): %', stray;
    END IF;
END $$;

UPDATE skills s
   SET category = c.category
  FROM _cats c
 WHERE s.skill_name = c.skill_name
   AND s.category IS DISTINCT FROM c.category;

-- Nothing here may CLEAR a category, and every listed name that exists as a
-- skill must now carry one. A name absent from skills entirely is fine --
-- it simply has not been scraped yet, and the dict entry will catch it when
-- it is.
DO $$
DECLARE still_null int; overwritten int;
BEGIN
    SELECT COUNT(*) INTO still_null
      FROM _cats c JOIN skills s ON s.skill_name = c.skill_name
     WHERE s.category IS NULL;
    IF still_null > 0 THEN
        RAISE EXCEPTION '% listed skill(s) are still uncategorised', still_null;
    END IF;

    SELECT COUNT(*) INTO overwritten
      FROM _cats c JOIN skills s ON s.skill_name = c.skill_name
     WHERE s.category <> c.category;
    IF overwritten > 0 THEN
        RAISE EXCEPTION '% skill(s) disagree with the intended category', overwritten;
    END IF;
END $$;

COMMIT;

SELECT COALESCE(category, '(uncategorised)') AS category, COUNT(*) AS skills
  FROM skills GROUP BY 1 ORDER BY skills DESC;
