-- ---------------------------------------------------------------------
-- Split cleaned_postings by how often each column is written.
--
-- Measured 2026-09-15 on the live 920 rows: the spine carried 1,194 bytes
-- across 45 columns and fitted 4.4 rows per 8 kB page, so a re-sighting that
-- only bumped last_seen_date rewrote the whole 2,230-byte tuple. Three
-- satellites fix that:
--
--   posting_state      url + expiry. Written by the liveness job, and by a
--                      scrape only to clear a stale expiry.
--   posting_content    description. 466 of those 1,194 bytes, read on the
--                      detail path alone.
--   posting_sightings  everything a re-sighting changes. ~54 bytes, so the
--                      daily write drops from ~2,230 bytes to ~77.
--
-- Six array columns are DROPPED outright rather than moved: skill_ids,
-- preferred_skill_ids, skill_groups, accepted_degree_ids,
-- accepted_degree_specialization_ids and city_ids were byte-identical copies
-- of posting_skills / posting_qualification_* / posting_cities -- verified
-- 0 of 918 rows differing on 2026-09-15. Their five GIN indexes go with them;
-- the working copies on posting_skills stay untouched and still serve the
-- ?skill= filter, which is the 14x case postings.py documents.
--
-- posting_state.source is a deliberate duplicate of cleaned_postings.source.
-- It never changes after insert, and without it the liveness queue would join
-- back to the spine purely to filter on it -- measured 38 pages against 274.
--
-- Idempotent. Safe to re-run. Wrap in a transaction so a failure leaves
-- nothing half-moved.
-- ---------------------------------------------------------------------

BEGIN;

-- --- 1. the satellites -------------------------------------------------

CREATE TABLE IF NOT EXISTS posting_state (
    job_id          BIGINT PRIMARY KEY
                    REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    is_expired      BOOLEAN,
    expired_on      DATE,
    last_checked_on DATE,
    expiry_basis    TEXT,
    CONSTRAINT posting_state_source_check
        CHECK (source IN ('naukri', 'hirist', 'other')),
    CONSTRAINT posting_state_expiry_basis_check
        CHECK (expiry_basis IS NULL OR expiry_basis IN ('observed', 'delisted')),
    CONSTRAINT posting_state_expired_on_check
        CHECK (expired_on IS NULL OR is_expired IS TRUE),
    CONSTRAINT posting_state_basis_pair_check
        CHECK ((is_expired IS TRUE) = (expiry_basis IS NOT NULL))
);

-- description only. description_foreign_cities stays on the spine: it is a
-- 13-byte flag ABOUT the posting that ?description_flagged= filters list
-- queries on, and moving it here would make every list request join the widest
-- table in the schema to read it.
CREATE TABLE IF NOT EXISTS posting_content (
    job_id      BIGINT PRIMARY KEY
                REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS posting_sightings (
    job_id                    BIGINT PRIMARY KEY
                              REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    last_seen_date            DATE NOT NULL DEFAULT CURRENT_DATE,
    times_seen                INTEGER NOT NULL DEFAULT 1,
    applicant_count           INTEGER,
    applicant_count_qualifier TEXT,
    -- plain NUMERIC, matching the column it replaces: a narrower type would
    -- reject a rating the source is free to widen.
    company_rating            NUMERIC,
    company_reviews           INTEGER,
    company_badges            TEXT[]
);

-- --- 2. move the data --------------------------------------------------
-- Guarded on the source column still existing, so a re-run after step 3 is
-- a no-op rather than an error.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'cleaned_postings' AND column_name = 'url') THEN

        INSERT INTO posting_state
            (job_id, source, url, is_expired, expired_on, last_checked_on, expiry_basis)
        SELECT job_id, source, url, is_expired, expired_on, last_checked_on, expiry_basis
        FROM cleaned_postings
        ON CONFLICT (job_id) DO NOTHING;

        INSERT INTO posting_content (job_id, description)
        SELECT job_id, description
        FROM cleaned_postings
        ON CONFLICT (job_id) DO NOTHING;

        INSERT INTO posting_sightings
            (job_id, last_seen_date, times_seen, applicant_count,
             applicant_count_qualifier, company_rating, company_reviews, company_badges)
        SELECT job_id, last_seen_date, times_seen, applicant_count,
               applicant_count_qualifier, company_rating, company_reviews, company_badges
        FROM cleaned_postings
        ON CONFLICT (job_id) DO NOTHING;
    END IF;
END $$;

-- --- 3. indexes the satellites need ------------------------------------
-- The liveness queue filters source + expiry and reads url; this covers it
-- without touching the spine at all.
CREATE INDEX IF NOT EXISTS idx_posting_state_queue
    ON posting_state (source, last_checked_on)
    WHERE is_expired IS NOT TRUE;

-- snapshot_daily_skills() selects the postings seen today. A sequential scan
-- of the spine was 416 pages; this reads an index over ~11.
CREATE INDEX IF NOT EXISTS idx_posting_sightings_last_seen
    ON posting_sightings (last_seen_date);

-- ?skills_all= asks for containment over the CONCATENATION of the two skill
-- arrays. Neither existing GIN index covers that expression -- proven on
-- 2026-09-15: it sequential-scans even with enable_seqscan off. This is the
-- only index that can serve it.
CREATE INDEX IF NOT EXISTS idx_posting_skills_all
    ON posting_skills USING GIN ((skill_ids || skill_group_ids(skill_groups)));

-- --- 4. drop what moved, and what was already duplicated ---------------

ALTER TABLE cleaned_postings
    DROP COLUMN IF EXISTS url,
    DROP COLUMN IF EXISTS description,
    DROP COLUMN IF EXISTS is_expired,
    DROP COLUMN IF EXISTS expired_on,
    DROP COLUMN IF EXISTS last_checked_on,
    DROP COLUMN IF EXISTS expiry_basis,
    DROP COLUMN IF EXISTS last_seen_date,
    DROP COLUMN IF EXISTS times_seen,
    DROP COLUMN IF EXISTS applicant_count,
    DROP COLUMN IF EXISTS applicant_count_qualifier,
    DROP COLUMN IF EXISTS company_rating,
    DROP COLUMN IF EXISTS company_reviews,
    DROP COLUMN IF EXISTS company_badges,
    -- already in posting_skills, byte-identical on every row
    DROP COLUMN IF EXISTS skill_ids,
    DROP COLUMN IF EXISTS preferred_skill_ids,
    DROP COLUMN IF EXISTS skill_groups,
    -- already in posting_qualification_degrees / _specializations
    DROP COLUMN IF EXISTS accepted_degree_ids,
    DROP COLUMN IF EXISTS accepted_degree_specialization_ids,
    -- already in posting_cities
    DROP COLUMN IF EXISTS city_ids;

-- --- 5. reclaim, and leave room for HOT updates ------------------------
-- fillfactor stays at 70: measured 2026-09-11, it took HOT updates from 4%
-- to 81% on a wide row, and the satellites want the same headroom.
ALTER TABLE cleaned_postings  SET (fillfactor = 70);
ALTER TABLE posting_state     SET (fillfactor = 70);
ALTER TABLE posting_content   SET (fillfactor = 70);
ALTER TABLE posting_sightings SET (fillfactor = 70);

COMMIT;

-- VACUUM FULL cannot run inside a transaction, so it is deliberately left to
-- the caller:
--     VACUUM FULL cleaned_postings;
--     ANALYZE;
