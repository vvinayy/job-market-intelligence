-- ---------------------------------------------------------------------
-- Clear preferred skills written from hirist's isMandatory tags.
--
-- preferred_skill_ids means "the employer starred this as PREFERRED" -- the
-- optional half of a posting's requirements. hirist publishes isMandatory,
-- which is close to the opposite statement. An early version of
-- hirist_collector.build_record() mapped one onto the other; the collector
-- was corrected, but the rows already written were not.
--
-- The result was one column carrying two contradictory meanings, split by
-- scrape date: 19 postings first seen 2026-09-02 hold mandatory tags, the 40
-- first seen 2026-09-03 correctly hold none. Verified against the live API --
-- the stored values match each posting's isMandatory tags exactly.
--
-- It surfaces as "preferred_skills" on /postings/{id} and as the star in the
-- Jobs detail panel, so a reader was being told a required skill was
-- optional.
--
-- Nothing is lost. Every one of those skills is still in skill_ids, which is
-- where a mandatory skill belongs; only the claim about it being *preferred*
-- goes away. Clearing is the honest repair rather than re-deriving, because
-- hirist publishes no preferred signal at all -- so the true value is empty.
--
-- Both places the fact lives are cleared. posting_skills is what the API
-- reads; cleaned_postings is what production computes off directly.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-03-clear-hirist-preferred-skills.sql
-- ---------------------------------------------------------------------

BEGIN;

UPDATE posting_skills ps
   SET preferred_skill_ids = '{}'
  FROM cleaned_postings c
 WHERE c.job_id = ps.job_id
   AND c.source = 'hirist'
   AND cardinality(ps.preferred_skill_ids) > 0;

UPDATE cleaned_postings
   SET preferred_skill_ids = '{}'
 WHERE source = 'hirist'
   AND cardinality(preferred_skill_ids) > 0;

-- Refuse to commit a half-done repair, and refuse to commit if this ever
-- touched a board that does publish a real preferred signal.
DO $$
DECLARE remaining int; naukri_lost int;
BEGIN
    SELECT COUNT(*) INTO remaining
      FROM cleaned_postings c
      LEFT JOIN posting_skills ps ON ps.job_id = c.job_id
     WHERE c.source = 'hirist'
       AND (cardinality(c.preferred_skill_ids) > 0
            OR cardinality(COALESCE(ps.preferred_skill_ids, '{}')) > 0);
    IF remaining > 0 THEN
        RAISE EXCEPTION 'still % hirist row(s) carrying preferred skills', remaining;
    END IF;

    SELECT COUNT(*) INTO naukri_lost
      FROM cleaned_postings WHERE source = 'naukri'
       AND cardinality(preferred_skill_ids) > 0;
    IF naukri_lost = 0 THEN
        RAISE EXCEPTION 'naukri preferred skills were cleared too -- aborting';
    END IF;
END $$;

COMMIT;

SELECT source,
       COUNT(*) AS postings,
       COUNT(*) FILTER (WHERE cardinality(preferred_skill_ids) > 0) AS with_preferred
  FROM cleaned_postings GROUP BY source ORDER BY source;
