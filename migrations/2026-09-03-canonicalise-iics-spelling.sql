-- ---------------------------------------------------------------------
-- Rename the skill 'Iics' to 'IICS', its canonical spelling.
--
-- skills auto-registers whatever normalize_skill() produces. IICS entered the
-- taxonomy in 96ada74 (2026-09-02) with the canonical spelling 'IICS'; the
-- four postings that mentioned it earlier had registered the title-case
-- fallback 'Iics'. Two spellings, one skill.
--
-- Left alone, the next extraction that finds it would INSERT 'IICS' beside
-- 'Iics' and split its demand across two ids -- which is exactly what already
-- happened to JPA (ids 983 'Jpa' and 1380 'JPA', 3 and 4 postings). Renaming
-- in place keeps skill_id 499, so every posting_skills and cleaned_postings
-- reference stays valid and nothing needs rewriting.
--
-- skill_daily_counts is NOT touched. It stores the name observed on the day,
-- and those rows are observations -- 'Iics' is what the pipeline recorded on
-- 14-17 Aug and on 02 Sep, and that stays true. The series will show a step
-- to 'IICS', which is the instrument changing, not the market.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-03-canonicalise-iics-spelling.sql
-- ---------------------------------------------------------------------

DO $$
BEGIN
    -- Only safe while 'IICS' does not already exist; if it does this needs to
    -- be a merge, not a rename, and that is a different migration.
    IF EXISTS (SELECT 1 FROM skills WHERE skill_name = 'IICS')
       AND EXISTS (SELECT 1 FROM skills WHERE skill_name = 'Iics') THEN
        RAISE EXCEPTION 'both spellings exist -- needs a merge, not a rename';
    END IF;

    UPDATE skills SET skill_name = 'IICS' WHERE skill_name = 'Iics';
END $$;

SELECT skill_id, skill_name FROM skills WHERE lower(skill_name) = 'iics';
