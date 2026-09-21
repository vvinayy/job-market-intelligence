-- ---------------------------------------------------------------------
-- Add posting_skill_demand: one copy of "every skill this posting would
-- accept", replacing the hand-written concatenation at every read path
-- that wants skills as ROWS.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-21-posting-skill-demand-view.sql
--
-- WHY. `skill_ids || skill_group_ids(skill_groups)` was written out by
-- hand at twelve sites across four files. That is how the 2026-09-17 bug
-- happened -- eight read paths carried only the first half and reported
-- AWS in 295 postings while ?skill=AWS returned 419 -- and it is how a
-- second, smaller one happened since, described below.
--
-- WHAT IS ACTUALLY WRONG TODAY. The two columns are meant to be disjoint.
-- On four rows they are not:
--
--     job 2250  Django      in skill_ids AND in a skill_group
--     job 2695  Snowflake   ditto
--     job 2952  Terraform   ditto
--     job 2963  Terraform   ditto
--
-- These came out of migrations/2026-09-16-merge-duplicate-skill-spellings.sql.
-- It repointed losing spellings into their canonical id inside skill_ids
-- ("Iac Terraform" -> Terraform, "Snowflake Db" -> Snowflake, "Django
-- Framework" -> Django) and verified afterwards that no id appeared twice
-- within skill_ids -- but never checked whether the id it had just written
-- was already sitting in skill_groups on the same row. It was.
--
-- Concatenating then produces the skill twice, so every COUNT(*) read path
-- over-counted: Terraform 150 against a true 148, Django 107 against 106,
-- Snowflake 67 against 66.
--
-- WHY THE DATA IS NOT REPAIRED IN PLACE. A posting demanding Terraform
-- outright and also listing it among alternatives is a real shape, not
-- corruption. Dropping it from skill_ids would discard a genuine outright
-- requirement; dropping it from skill_groups would misrepresent the choice
-- the employer offered. Both copies are true, so the duplicate is removed
-- where it is read, not where it is stored.
--
-- NO INSTRUMENT STEP. snapshot_daily_skills() has always counted
-- COUNT(DISTINCT c.job_id), so the duplicate collapsed before anything
-- reached skill_daily_counts. The stored history is correct and unchanged;
-- only live-computed endpoints move, and those keep no history to
-- disagree with. This needs no skill_daily_corrections row and no
-- instrument_changes row -- nothing recorded was ever wrong.
-- ---------------------------------------------------------------------

BEGIN;

CREATE OR REPLACE VIEW posting_skill_demand AS
    SELECT DISTINCT ps.job_id, u.skill_id
      FROM posting_skills ps,
           unnest(ps.skill_ids || skill_group_ids(ps.skill_groups)) AS u(skill_id);

-- Verification. The view must drop exactly the known duplicates and
-- nothing else -- if it removes more, the disjointness assumption has
-- broken somewhere this migration has not looked at.
DO $$
DECLARE raw_mentions int; view_mentions int; overlap_rows int;
BEGIN
    SELECT COALESCE(SUM(array_length(skill_ids || skill_group_ids(skill_groups), 1)), 0)
      INTO raw_mentions FROM posting_skills;
    SELECT COUNT(*) INTO view_mentions FROM posting_skill_demand;
    SELECT COUNT(*) INTO overlap_rows FROM posting_skills
     WHERE skill_ids && skill_group_ids(skill_groups);

    RAISE NOTICE 'skill mentions: % raw -> % deduplicated (% row(s) overlap)',
                 raw_mentions, view_mentions, overlap_rows;

    IF raw_mentions - view_mentions <> 4 THEN
        RAISE EXCEPTION 'expected exactly 4 duplicate mentions, found %',
                        raw_mentions - view_mentions;
    END IF;

    -- Every posting that has skills must still be represented.
    IF (SELECT COUNT(DISTINCT job_id) FROM posting_skill_demand)
       <> (SELECT COUNT(*) FROM posting_skills WHERE skill_ids <> '{}'
                                                  OR skill_groups <> '[]'::jsonb) THEN
        RAISE EXCEPTION 'the view lost or invented a posting';
    END IF;
END $$;

COMMIT;

\echo
\echo 'Top skills, corrected -- Terraform 148, Django 106, Snowflake 66:'
SELECT sk.skill_name, COUNT(*)::int AS postings
  FROM posting_skill_demand d
  JOIN skills sk ON sk.skill_id = d.skill_id
 WHERE sk.skill_name IN ('Terraform', 'Django', 'Snowflake')
 GROUP BY 1 ORDER BY 2 DESC;
