-- ---------------------------------------------------------------------
-- A place to record what a hirist liveness check WOULD have written.
--
-- The rule (liveness.classify_hirist) is measured but not trusted yet: the
-- 2026-09-02 census separated 20 live postings from 20 dead ones perfectly,
-- but never watched a single posting cross from one state to the other, and
-- that transition is the thing an actual checker depends on.
--
-- So this table exists to earn that evidence without betting the data on it.
-- hirist_liveness_probe.py writes here and NOWHERE else -- it never touches
-- cleaned_postings.is_expired -- so a wrong rule costs a wrong row in an
-- observation log rather than a fabricated closure that then counts as
-- exposure in /analytics/closures and depresses every rate.
--
-- One row per posting per day. When a job_id's verdict changes between two
-- observed_on dates, that is the transition, and the rule can be promoted.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-03-hirist-liveness-observations.sql
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hirist_liveness_observations (
    observed_on   DATE   NOT NULL DEFAULT CURRENT_DATE,
    job_id        BIGINT NOT NULL REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    job_code      TEXT,
    http_status   INT,
    -- The raw field, kept beside the verdict it produced. NULL means the
    -- payload did not carry it, which is not the same as false.
    has_expired   BOOLEAN,
    verdict       TEXT NOT NULL CHECK (verdict IN ('expired', 'live', 'unknown')),
    PRIMARY KEY (observed_on, job_id)
);

-- "Has this posting's verdict changed?" is the only question this table is
-- for, and it is asked per posting across dates.
CREATE INDEX IF NOT EXISTS idx_hirist_obs_job
    ON hirist_liveness_observations (job_id, observed_on);

SELECT COUNT(*) AS observations_recorded FROM hirist_liveness_observations;
