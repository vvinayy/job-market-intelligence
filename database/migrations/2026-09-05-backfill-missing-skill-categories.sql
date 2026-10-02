-- ---------------------------------------------------------------------
-- Give eight skills the category they should always have had.
--
-- skills.category is written ONCE, by cleaning.categorize_skill() at the
-- moment a skill is first registered, and nothing ever refreshes it. So an
-- entry added to SKILL_CATEGORIES later never reaches the rows already
-- there. This file is that refresh.
--
-- Two separate omissions, both dated 2026-09-02:
--
--   * Seven of the fourteen vocabulary entries added that day went into
--     SKILL_ALIASES but not into SKILL_CATEGORIES, so they registered with
--     no category: Data Ingestion, Data Integration, IICS, Matillion,
--     Hibernate, JPA, Distributed Systems.
--
--   * Data Pipeline is the same omission wearing a plural. SKILL_CATEGORIES
--     held the key "Data Pipelines", which normalize_skill() folds to the
--     singular -- so the key could never match anything, and the single
--     largest uncategorised skill (112 postings) went unclassified.
--
-- Category is read by /analytics/skill-categories alone, which excludes
-- uncategorised skills rather than bucketing them, so this widens that
-- chart's coverage from 67% of skill mentions to about 73%. It changes
-- nothing about filtering, demand ranking or trends.
--
-- Written out rather than derived so this file keeps meaning what it meant
-- the day it ran. Nothing here loses a category -- verified before writing.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-05-backfill-missing-skill-categories.sql
-- ---------------------------------------------------------------------

BEGIN;

UPDATE skills s
   SET category = m.category
  FROM (VALUES
        ('Data Ingestion',      'Data/ML'),
        ('Data Integration',    'Data/ML'),
        ('Data Pipeline',       'Data/ML'),
        ('IICS',                'Data/ML'),
        ('Matillion',           'Data/ML'),
        ('Hibernate',           'Backend'),
        ('JPA',                 'Backend'),
        ('Distributed Systems', 'Backend')
       ) AS m(skill_name, category)
 WHERE s.skill_name = m.skill_name
   AND s.category IS DISTINCT FROM m.category;

-- Refuse to commit if this somehow cleared a category instead of setting one.
DO $$
DECLARE missing int;
BEGIN
    SELECT COUNT(*) INTO missing FROM (VALUES
        ('Data Ingestion'), ('Data Integration'), ('Data Pipeline'), ('IICS'),
        ('Matillion'), ('Hibernate'), ('JPA'), ('Distributed Systems')
    ) AS w(skill_name)
    WHERE NOT EXISTS (SELECT 1 FROM skills s
                       WHERE s.skill_name = w.skill_name AND s.category IS NOT NULL);
    IF missing > 0 THEN
        RAISE EXCEPTION '% skill(s) still uncategorised after the update', missing;
    END IF;
END $$;

COMMIT;

SELECT COALESCE(category, '(none)') AS category, COUNT(*) AS skills
  FROM skills GROUP BY 1 ORDER BY skills DESC;
