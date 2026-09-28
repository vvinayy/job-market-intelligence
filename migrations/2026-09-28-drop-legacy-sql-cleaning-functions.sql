-- ---------------------------------------------------------------------
-- Drop the 8 legacy SQL cleaning functions.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-28-drop-legacy-sql-cleaning-functions.sql
--
-- Commit 59f9094 (2026-08-14) moved cleaning from PL/pgSQL into cleaning.py
-- and removed these from the schema files, but never dropped them from the
-- live database -- so they were the only functions differing between
-- jobmarket and a fresh schema.sql + trends_setup.sql build (checked
-- 2026-09-28 against the catalogs).
--
-- Dead, verified before dropping: nothing in pg_depend references them, no
-- view or other function outside this set calls them, and no .py, .bat or
-- .sql in the repo invokes them as SQL. clean_and_populate() reads
-- raw_postings, which no longer exists, so it could only error. And
-- normalize_working_type() still carried the ELSE 'On-site' fallback that
-- fabricated a value on 372 of 495 rows -- the reference failure in
-- CLAUDE.md -- one accidental call away.
--
-- REVERT: migrations/2026-09-28-restore-legacy-sql-cleaning-functions.sql
-- holds their exact definitions, captured just before this ran.
-- ---------------------------------------------------------------------

BEGIN;

DROP FUNCTION classify_role(raw_title text),
              clean_and_populate(),
              normalize_skill(raw_skill text),
              normalize_working_type(raw text),
              parse_contract_type(raw text),
              parse_employment_type(raw text),
              parse_range_max(raw text),
              parse_range_min(raw text);

-- RESTRICT (the default) means this fails, and the transaction rolls back,
-- if anything turns out to depend on one of them after all.

COMMIT;
