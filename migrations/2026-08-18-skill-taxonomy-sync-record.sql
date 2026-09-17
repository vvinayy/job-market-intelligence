-- ---------------------------------------------------------------------
-- RECORD ONLY — this is not a migration to run. It is a written record of
-- work that was already done directly against the live database on
-- 2026-08-18, with no SQL file saved at the time. Written retroactively on
-- 2026-09-17 while investigating why 118 old skill spellings had no
-- migration explaining their disappearance from `skills` -- most of them
-- trace back to this date.
--
-- Source of truth: commit 755fa6f717b6df5e43cabc597ff93c1d60fffab5,
-- "Remove role_category; fix skill taxonomy sync between key_skills and
-- description mining" (2026-08-18). That commit also removed the
-- role_category column entirely -- unrelated to skills, already reflected
-- in schema.sql, and not covered here.
--
-- WHAT HAPPENED, per the commit message:
--
-- Before touching anything, live category coverage was measured at 86 of
-- 1,016 distinct skills (8.5%), down from an earlier ~50% estimate --
-- every scrape auto-registers new skill names and the category dict never
-- kept pace. Pulling the 40 highest-frequency uncategorized skills found
-- two distinct causes:
--
--   1. A same-file typo: SKILL_CATEGORIES had "Shell scripting" cased
--      inconsistently with what the taxonomy's own canonical spelling and
--      _initcap() actually produced.
--   2. The real bug: normalize_skill() only resolved key_skills (Naukri's
--      raw tags) against cleaning.py's own small SKILL_ALIASES. Mined
--      description text already got skill_taxonomy.py's larger alias
--      table via extract_skills(), but key_skills never did -- so the
--      same tool landed under two different castings depending on which
--      source mentioned it ("Fastapi" from a tag vs "FastAPI" from mined
--      text), and only one spelling matched SKILL_CATEGORIES' key.
--
-- Fixed by merging skill_taxonomy.SKILL_ALIASES into cleaning.py's lookup.
-- Also added vocabulary gaps found in the same pull: Data Science (the
-- single highest-frequency uncategorized term, 49 postings), Data
-- Engineering, Data Analytics, Data Analysis, Data Modeling, Big Data,
-- Generative AI, Deep Learning -- all still present today.
--
-- The live `skills` table was then backfilled under the corrected logic
-- (a pg_dump backup was taken first): simulating normalize_skill() /
-- categorize_skill() against every existing skill_name surfaced 17 genuine
-- duplicate pairs already sitting in the table under two castings each --
-- example given: skill_id 800 "Fastapi" and 703 "Fast Api" both
-- correcting to "FastAPI". Each pair was merged by repointing every
-- posting_skills.skill_ids reference from the loser id to the winner
-- (deduping per posting where both were already present) and deleting the
-- loser row -- the same mechanism the 2026-09-04 and 2026-09-16 merge
-- migrations use. 23 more names were simple renames (one spelling only,
-- UPDATE skill_name in place, same mechanism as
-- 2026-09-03-canonicalise-iics-spelling.sql). 7 gained a category with no
-- rename needed.
--
-- Result: skills 1,016 -> 998 rows (18 duplicates removed by merge),
-- categorized 86 -> 120 (8.5% -> 12.0%).
--
-- Verified at the time: py_compile; zero dangling posting_skills
-- references to deleted skill_ids; five spot-checked entries (FastAPI,
-- Shell scripting, SQL Server, Data Science, Big Data) categorized
-- correctly; the live API across /postings, /analytics/summary,
-- /analytics/skills on a throwaway port; all 5 dashboard pages via
-- AppTest; a real save_records() call with a mis-cased "Fastapi" tag
-- confirming it resolved to the existing FastAPI/Backend row instead of
-- creating a duplicate.
--
-- WHY THIS FILE CANNOT DO WHAT THE OTHER MERGE MIGRATIONS DO. The 18
-- merged pairs and 23 renamed names were never written out as a VALUES
-- list -- the commit message documents the METHOD and the counts, not the
-- individual names, and the source data (the pre-merge `skills` rows) is
-- gone. Re-deriving the exact list from today's database is not reliable:
-- more renames and merges have happened since (2026-09-03, 2026-09-04,
-- 2026-09-16), so a name absent from `skills` today may be absent for a
-- LATER reason, not this one. This file records what happened and why it
-- cannot be replayed -- it does not attempt to reconstruct the pair list.
--
-- WHAT THIS MEANS FOR skill_daily_counts and skill_renames. Names orphaned
-- by this cleanup are exactly the kind of thing skill_renames
-- (migrations/2026-09-17-skill-renames.sql) exists to reconnect -- and 20
-- of that migration's 27 pairs likely originate here, identified
-- independently by comparing today's stranded names against
-- normalize_skill()'s current output, not by consulting this record. That
-- migration is the operative fix; this file is provenance for why the gap
-- existed.
--
-- Nothing below changes data. It only confirms the description above is
-- still consistent with the live table.
--   psql -U postgres -d jobmarket -f migrations/2026-08-18-skill-taxonomy-sync-record.sql
-- ---------------------------------------------------------------------

\echo Spot-check from the commit message: the FastAPI merge
SELECT skill_id, skill_name, category FROM skills WHERE skill_name = 'FastAPI';
-- Expect: skill_id 703, category Backend. Its losing pair, "Fastapi"
-- (skill_id 800), and the other old casing "Fast Api", must both be gone.
SELECT count(*) AS should_be_zero FROM skills WHERE skill_name IN ('Fastapi', 'Fast Api');

\echo
\echo Vocabulary added in this pass -- all should still resolve
SELECT skill_name, category FROM skills
 WHERE skill_name IN ('Data Science','Data Engineering','Data Analytics',
                      'Data Analysis','Data Modeling','Big Data',
                      'Generative AI','Deep Learning','Shell scripting')
 ORDER BY 1;
