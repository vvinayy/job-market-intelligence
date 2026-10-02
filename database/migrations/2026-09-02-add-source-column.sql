-- ---------------------------------------------------------------------
-- Add cleaned_postings.source — which job board a posting came from.
--
-- schema.sql carries this column for a fresh install, but it DROPS the
-- postings tables on the way in, so an existing database cannot be brought
-- forward by running it. This file is that path: idempotent, safe to re-run,
-- and it writes no job data of its own — every value is derived from `url`,
-- which is NOT NULL on every row.
--
-- Keep the CASE below in step with cleaning.SOURCE_HOSTS. It is written out
-- once here rather than shared because a migration has to keep working
-- against the rule as it stood the day it ran.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-02-add-source-column.sql
-- ---------------------------------------------------------------------

ALTER TABLE cleaned_postings ADD COLUMN IF NOT EXISTS source TEXT;

-- Backfill. WHERE source IS NULL so a re-run costs nothing and, more to the
-- point, never overwrites a value the pipeline has since written.
UPDATE cleaned_postings
   SET source = CASE WHEN url ILIKE '%naukri.com%' THEN 'naukri'
                     WHEN url ILIKE '%hirist%'     THEN 'hirist'
                     ELSE 'other' END
 WHERE source IS NULL;

ALTER TABLE cleaned_postings ALTER COLUMN source SET DEFAULT 'other';
ALTER TABLE cleaned_postings ALTER COLUMN source SET NOT NULL;

-- ADD CONSTRAINT has no IF NOT EXISTS, so it is guarded rather than repeated.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'cleaned_postings_source_check') THEN
        ALTER TABLE cleaned_postings
            ADD CONSTRAINT cleaned_postings_source_check
            CHECK (source IN ('naukri', 'hirist', 'other'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_cleaned_postings_source
    ON cleaned_postings (source);

-- What landed.
SELECT source, COUNT(*) AS postings
  FROM cleaned_postings
 GROUP BY source
 ORDER BY postings DESC;
