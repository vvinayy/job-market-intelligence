-- ---------------------------------------------------------------------
-- Merge the duplicate JPA skill rows.
--
-- skills auto-registers whatever normalize_skill() emits. JPA entered the
-- taxonomy with the canonical spelling 'JPA' on 2026-09-02 (96ada74); the
-- postings that mentioned it before that had registered the title-case
-- fallback 'Jpa'. Two ids, one skill: 983 'Jpa' on 3 postings and 1380 'JPA'
-- on 4, so demand for JPA reads as 4 when it is 7.
--
-- This is the same defect the Iics rename avoided. A rename was enough there
-- because only one spelling existed; here both do, so the references have to
-- move before the losing row can go.
--
-- Verified before writing: 983 appears only in skill_ids (never in
-- skill_groups, never in preferred_skill_ids, not in skill_blocklist), and no
-- posting carries both ids -- so a straight replacement cannot produce a
-- duplicate inside an array. skills has no foreign keys pointing at it;
-- Postgres cannot constrain individual array elements, which is exactly why
-- this has to be done by hand rather than by ON UPDATE CASCADE.
--
-- skill_daily_counts is NOT touched. It stores the name observed on the day.
-- 'Jpa' ran 20 Aug to 02 Sep and 'JPA' from 03 Sep; that step is the
-- instrument changing and it is a true record of what the pipeline saw.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-04-merge-duplicate-jpa-skill.sql
-- ---------------------------------------------------------------------

BEGIN;

DO $$
DECLARE keep int; drop_id int; overlap int;  -- not `both`: reserved in PL/pgSQL
BEGIN
    SELECT skill_id INTO keep    FROM skills WHERE skill_name = 'JPA';
    SELECT skill_id INTO drop_id FROM skills WHERE skill_name = 'Jpa';
    IF keep IS NULL OR drop_id IS NULL THEN
        RAISE NOTICE 'nothing to merge -- one spelling is already gone';
        RETURN;
    END IF;

    SELECT COUNT(*) INTO overlap FROM cleaned_postings
     WHERE drop_id = ANY(skill_ids || skill_group_ids(skill_groups))
       AND keep    = ANY(skill_ids || skill_group_ids(skill_groups));
    IF overlap > 0 THEN
        RAISE EXCEPTION '% posting(s) hold both ids -- a replace would duplicate', overlap;
    END IF;

    UPDATE cleaned_postings SET skill_ids = array_replace(skill_ids, drop_id, keep)
     WHERE drop_id = ANY(skill_ids);
    UPDATE cleaned_postings
       SET preferred_skill_ids = array_replace(preferred_skill_ids, drop_id, keep)
     WHERE drop_id = ANY(preferred_skill_ids);
    UPDATE posting_skills SET skill_ids = array_replace(skill_ids, drop_id, keep)
     WHERE drop_id = ANY(skill_ids);
    UPDATE posting_skills
       SET preferred_skill_ids = array_replace(preferred_skill_ids, drop_id, keep)
     WHERE drop_id = ANY(preferred_skill_ids);

    -- Groups are JSONB, so a replace means rebuilding each array. Guarded
    -- rather than assumed: today no group holds it, and if that ever changes
    -- this refuses instead of silently leaving a dangling id behind.
    IF EXISTS (SELECT 1 FROM cleaned_postings
                WHERE drop_id = ANY(skill_group_ids(skill_groups)))
       OR EXISTS (SELECT 1 FROM posting_skills
                   WHERE drop_id = ANY(skill_group_ids(skill_groups))) THEN
        RAISE EXCEPTION 'id % appears inside skill_groups -- needs JSONB rewriting', drop_id;
    END IF;

    DELETE FROM skills WHERE skill_id = drop_id;
END $$;

DO $$
DECLARE dangling int;
BEGIN
    SELECT COUNT(*) INTO dangling FROM (
        SELECT unnest(skill_ids || skill_group_ids(skill_groups) || preferred_skill_ids) AS sid
          FROM cleaned_postings) x
     WHERE NOT EXISTS (SELECT 1 FROM skills s WHERE s.skill_id = x.sid);
    IF dangling > 0 THEN
        RAISE EXCEPTION '% dangling skill reference(s) after the merge', dangling;
    END IF;
END $$;

COMMIT;

SELECT skill_id, skill_name,
       (SELECT COUNT(*) FROM cleaned_postings c
         WHERE s.skill_id = ANY(c.skill_ids || skill_group_ids(c.skill_groups))) AS postings
  FROM skills s WHERE lower(skill_name) = 'jpa';
