-- ---------------------------------------------------------------------
-- Merge skill rows whose spelling the taxonomy has since superseded.
--
-- skills auto-registers whatever normalize_skill() emits, so a name entering
-- SKILL_ALIASES later collides with the title-case fallback already
-- registered for it. The 14 entries added on 2026-09-02 (96ada74) did exactly
-- that, and LLM ended up with FOUR identities:
--
--   Data Pipelines (633)        -> Data Pipeline (832)
--   Gen Ai (842)                -> Generative AI (516)
--   Large Language Models (256) -> LLM (680)
--   Large Language Model (1134) -> LLM (680)
--   Llms (1094)                 -> LLM (680)
--
-- Every one splits demand: LLM read 62 postings while three more rows held
-- another 8 between them. This must run BEFORE any re-derivation, or fresh
-- extraction emits the canonical name and widens the split instead.
--
-- Unlike the JPA merge, array_replace alone is NOT safe here: 3 postings hold
-- both a losing id and its canonical one, so the replace is followed by a
-- DISTINCT and a sort, matching what the writer produces.
--
-- skill_daily_counts is NOT touched. It records the name observed on the day,
-- and those observations are true.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-04-merge-stale-skill-spellings.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE _merge ON COMMIT DROP AS
SELECT l.skill_id AS losing_id, k.skill_id AS keep_id, l.skill_name AS losing_name
  FROM (VALUES ('Data Pipelines','Data Pipeline'), ('Gen Ai','Generative AI'),
               ('Large Language Models','LLM'), ('Large Language Model','LLM'),
               ('Llms','LLM')) AS m(losing, canonical)
  JOIN skills l ON l.skill_name = m.losing
  JOIN skills k ON k.skill_name = m.canonical;

DO $$
DECLARE r record; in_groups int;
BEGIN
    SELECT COUNT(*) INTO in_groups FROM cleaned_postings c, _merge m
     WHERE m.losing_id = ANY(skill_group_ids(c.skill_groups));
    IF in_groups > 0 THEN
        RAISE EXCEPTION 'a losing id sits inside skill_groups -- needs JSONB rewriting';
    END IF;

    FOR r IN SELECT * FROM _merge LOOP
        -- BOTH columns in ONE statement. The preferred-subset CHECK is
        -- evaluated per row at the end of a statement, so updating skill_ids
        -- and preferred_skill_ids separately leaves the row momentarily
        -- inconsistent and the constraint fires -- which it did, on job 2612,
        -- whose preferred set held 1134 while skill_ids had just lost it.
        -- Neither order is safe: the other way round breaks a row whose
        -- canonical id is not yet in skill_ids.
        --
        -- Replace, then DISTINCT and sort: 3 rows already hold both ids, and a
        -- bare array_replace would leave the same id twice in one array.
        UPDATE cleaned_postings
           SET skill_ids = COALESCE(
                 (SELECT array_agg(DISTINCT id ORDER BY id)
                    FROM unnest(array_replace(skill_ids, r.losing_id, r.keep_id)) id), '{}'),
               preferred_skill_ids = COALESCE(
                 (SELECT array_agg(DISTINCT id ORDER BY id)
                    FROM unnest(array_replace(preferred_skill_ids, r.losing_id, r.keep_id)) id), '{}')
         WHERE r.losing_id = ANY(skill_ids)
            OR r.losing_id = ANY(preferred_skill_ids);

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
DECLARE dangling int; dupes int; broken int; disagreeing int;
BEGIN
    SELECT COUNT(*) INTO dangling FROM (
        SELECT unnest(skill_ids || skill_group_ids(skill_groups) || preferred_skill_ids) AS sid
          FROM cleaned_postings) x
     WHERE NOT EXISTS (SELECT 1 FROM skills s WHERE s.skill_id = x.sid);
    IF dangling > 0 THEN RAISE EXCEPTION '% dangling skill reference(s)', dangling; END IF;

    SELECT COUNT(*) INTO dupes FROM cleaned_postings c, _merge m
     WHERE cardinality(array_positions(c.skill_ids, m.keep_id)) > 1;
    IF dupes > 0 THEN RAISE EXCEPTION '% array(s) hold a merged id twice', dupes; END IF;

    SELECT COUNT(*) INTO broken FROM cleaned_postings
     WHERE NOT (preferred_skill_ids <@ (skill_ids || skill_group_ids(skill_groups)));
    IF broken > 0 THEN RAISE EXCEPTION '% row(s) break the preferred-subset CHECK', broken; END IF;

    SELECT COUNT(*) INTO disagreeing FROM cleaned_postings c
      JOIN posting_skills ps ON ps.job_id = c.job_id
     WHERE c.skill_ids <> ps.skill_ids OR c.preferred_skill_ids <> ps.preferred_skill_ids;
    IF disagreeing > 0 THEN
        RAISE EXCEPTION '% row(s) disagree between the two tables', disagreeing;
    END IF;
END $$;

COMMIT;

SELECT skill_id, skill_name,
       (SELECT COUNT(*) FROM cleaned_postings c
         WHERE s.skill_id = ANY(c.skill_ids || skill_group_ids(c.skill_groups))) AS postings
  FROM skills s
 WHERE skill_name IN ('LLM','Generative AI','Data Pipeline') ORDER BY skill_name;
