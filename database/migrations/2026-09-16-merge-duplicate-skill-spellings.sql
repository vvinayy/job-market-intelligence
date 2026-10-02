-- ---------------------------------------------------------------------
-- Collapse skill rows that are one skill stored under two or three names.
--
-- skills auto-registers whatever normalize_skill() emits, and anything not
-- in SKILL_ALIASES falls through to the _initcap fallback -- so "IaC
-- Terraform" registers beside "Terraform", and demand for one skill is
-- reported as demand for two. Found 2026-09-16 by collapsing every stored
-- name to lowercase-alphanumeric and looking for collisions: 14 groups.
--
-- Two kinds here, and the first matters more.
--
--   Folding into a name that ALREADY has a category -- this closes a
--   category gap as a side effect, because the merged postings inherit a
--   classified skill instead of an unclassified one:
--
--     Iac Terraform (23)        -> Terraform      [Cloud/DevOps]
--     Snowflake Db (12)         -> Snowflake      [Database]
--     Django Framework (10)     -> Django         [Backend]
--     Django Web Framework (2)  -> Django         [Backend]
--     Data Build Tool (10)      -> dbt            [Data/ML]
--     Reacts Js (9)             -> React          [Frontend]
--     Datalake (3)              -> Data Lake      [Data/ML]
--     Restapi (1)               -> REST API       [Backend]
--     Rabbitmq. (1)             -> RabbitMQ       [Backend]
--     Ml Ops (1)                -> MLOps          [Data/ML]
--
--   Pure spelling, both sides uncategorised. The canonical is whichever
--   spelling already holds the most postings, NOT the best-looking one:
--   renaming would mean rewriting skills.skill_name as well, which is a
--   larger change than the cosmetics justify.
--
--     Ci Cd Pipeline (23)          -> Ci/Cd Pipeline (34)
--     Ci/Cd Pipelines (15)         -> Ci/Cd Pipeline (34)
--     Cybersecurity (1)            -> Cyber Security (5)
--     Cloud Watch (1)              -> Cloudwatch (2)
--     Event Bridge (1)             -> Eventbridge (1)
--     Pl-Sql (1)                   -> PL/SQL (4)
--     Reactnative (1)              -> React Native (4)
--     Ab Testing (1)               -> A/B Testing (1)
--     Sales Force Development (1)  -> Salesforce Development (2)
--     Java Full Stack Developer(1) -> Java Fullstack Developer (2)
--
-- NOT merged, though the names look close: CI/CD (385) is left alone.
-- Folding "Ci/Cd Pipeline" into it would be a judgement that two names mean
-- the same skill, not an observation that one skill was spelled twice --
-- a different kind of claim, and one for a human to make deliberately.
-- Likewise Django Rest Api, Copilot Studio and Eks/Kubernetes are distinct
-- things, not misspellings of their neighbours.
--
-- Run AFTER the matching SKILL_ALIASES entries are in cleaning.py, or the
-- next scrape re-creates every row this deletes.
--
-- Targets posting_skills only. The 2026-09-04 version of this migration
-- also rewrote cleaned_postings.skill_ids / preferred_skill_ids; the
-- 2026-09-15 split deleted those columns, and posting_skills is now the
-- only home.
--
-- skill_daily_counts is NOT touched. It stores the name observed on the
-- day, and those observations were true when they were made.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-16-merge-duplicate-skill-spellings.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE _merge ON COMMIT DROP AS
SELECT l.skill_id AS losing_id, k.skill_id AS keep_id,
       l.skill_name AS losing_name, k.skill_name AS keep_name
  FROM (VALUES
        ('Iac Terraform',             'Terraform'),
        ('Snowflake Db',              'Snowflake'),
        ('Django Framework',          'Django'),
        ('Django Web Framework',      'Django'),
        ('Data Build Tool',           'dbt'),
        ('Reacts Js',                 'React'),
        ('Datalake',                  'Data Lake'),
        ('Restapi',                   'REST API'),
        ('Rabbitmq.',                 'RabbitMQ'),
        ('Ml Ops',                    'MLOps'),
        ('Ci Cd Pipeline',            'Ci/Cd Pipeline'),
        ('Ci/Cd Pipelines',           'Ci/Cd Pipeline'),
        ('Cybersecurity',             'Cyber Security'),
        ('Cloud Watch',               'Cloudwatch'),
        ('Event Bridge',              'Eventbridge'),
        ('Pl-Sql',                    'PL/SQL'),
        ('Reactnative',               'React Native'),
        ('Ab Testing',                'A/B Testing'),
        ('Sales Force Development',   'Salesforce Development'),
        ('Java Full Stack Developer', 'Java Fullstack Developer')
       ) AS m(losing, canonical)
  JOIN skills l ON l.skill_name = m.losing
  JOIN skills k ON k.skill_name = m.canonical;

DO $$
DECLARE r record; in_groups int; n_pairs int;
BEGIN
    SELECT COUNT(*) INTO n_pairs FROM _merge;
    RAISE NOTICE 'merging % pair(s)', n_pairs;

    -- skill_groups is JSONB (arrays of alternative skill ids). array_replace
    -- cannot reach inside it, so a losing id living there would survive the
    -- merge as a dangling reference. Measured 0 on 2026-09-16; this refuses
    -- to guess if that ever stops being true.
    SELECT COUNT(*) INTO in_groups
      FROM posting_skills ps, _merge m
     WHERE m.losing_id = ANY(skill_group_ids(ps.skill_groups));
    IF in_groups > 0 THEN
        RAISE EXCEPTION 'a losing id sits inside skill_groups -- needs JSONB rewriting';
    END IF;

    FOR r IN SELECT * FROM _merge LOOP
        -- BOTH columns in ONE statement. The preferred-subset CHECK is
        -- evaluated per row at the end of a statement, so updating skill_ids
        -- and preferred_skill_ids separately leaves the row momentarily
        -- inconsistent and the constraint fires. Neither order is safe.
        --
        -- Replace, then DISTINCT and sort: a posting may already hold BOTH
        -- the losing id and the canonical one, and a bare array_replace
        -- would leave the same id twice in one array.
        UPDATE posting_skills
           SET skill_ids = COALESCE(
                 (SELECT array_agg(DISTINCT id ORDER BY id)
                    FROM unnest(array_replace(skill_ids, r.losing_id, r.keep_id)) id), '{}'),
               preferred_skill_ids = COALESCE(
                 (SELECT array_agg(DISTINCT id ORDER BY id)
                    FROM unnest(array_replace(preferred_skill_ids, r.losing_id, r.keep_id)) id), '{}')
         WHERE r.losing_id = ANY(skill_ids)
            OR r.losing_id = ANY(preferred_skill_ids);

        DELETE FROM skills WHERE skill_id = r.losing_id;
    END LOOP;
END $$;

DO $$
DECLARE dangling int; dupes int; broken int;
BEGIN
    SELECT COUNT(*) INTO dangling FROM (
        SELECT unnest(skill_ids || skill_group_ids(skill_groups) || preferred_skill_ids) AS sid
          FROM posting_skills) x
     WHERE NOT EXISTS (SELECT 1 FROM skills s WHERE s.skill_id = x.sid);
    IF dangling > 0 THEN RAISE EXCEPTION '% dangling skill reference(s)', dangling; END IF;

    SELECT COUNT(*) INTO dupes FROM posting_skills ps, _merge m
     WHERE cardinality(array_positions(ps.skill_ids, m.keep_id)) > 1;
    IF dupes > 0 THEN RAISE EXCEPTION '% array(s) hold a merged id twice', dupes; END IF;

    SELECT COUNT(*) INTO broken FROM posting_skills
     WHERE NOT (preferred_skill_ids <@ (skill_ids || skill_group_ids(skill_groups)));
    IF broken > 0 THEN RAISE EXCEPTION '% row(s) break the preferred-subset CHECK', broken; END IF;

    IF EXISTS (SELECT 1 FROM skills s JOIN _merge m ON s.skill_id = m.losing_id) THEN
        RAISE EXCEPTION 'a losing skill row survived the delete';
    END IF;
END $$;

COMMIT;

SELECT s.skill_name, s.category,
       (SELECT COUNT(DISTINCT ps.job_id) FROM posting_skills ps
         WHERE s.skill_id = ANY(ps.skill_ids || skill_group_ids(ps.skill_groups))) AS postings
  FROM skills s
 WHERE s.skill_name IN ('Terraform','Snowflake','Django','dbt','React','Data Lake',
                        'REST API','RabbitMQ','MLOps','Ci/Cd Pipeline','Cyber Security',
                        'Cloudwatch','PL/SQL','React Native')
 ORDER BY postings DESC;
