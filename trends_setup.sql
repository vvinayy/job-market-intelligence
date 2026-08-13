-- =====================================================================
-- TREND LAYER
--
-- Run once to set up:
--   psql -U postgres -d jobmarket -f trends_setup.sql
--
-- Then, once per day AFTER clean_and_populate():
--   SELECT snapshot_daily_skills();
-- =====================================================================


-- ---------------------------------------------------------------------
-- SKILL BLOCKLIST — terms Naukri tags that aren't really skills.
--
-- Excluded from the trend VIEWS only. The snapshot still records them,
-- so if you later decide "Agile" is worth tracking, the history exists.
-- Filtering at capture would lose it permanently.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_blocklist (
    skill TEXT PRIMARY KEY
);

INSERT INTO skill_blocklist (skill) VALUES
    ('Agile'), ('Agile Methodologies'), ('Architecture'), ('Analytics'),
    ('Cloud'), ('Coding'), ('Testing'), ('Debugging'), ('Maintenance'),
    ('Business Requirements'), ('Software Design'), ('Code Quality'),
    ('Software Solutions'), ('Programming'), ('Programming Language'),
    ('Software Engineer'), ('Application Development'), ('Automation'),
    ('Monitoring'), ('Debt'), ('Asynchronous'), ('Basic'),
    ('Team Development'), ('Design Development'), ('Application Software'),
    ('Root Cause Analysis'), ('Data Preprocessing'), ('Compliance'),
    ('Workflow'), ('Scheduling'), ('Analytical'), ('It Services')
ON CONFLICT (skill) DO NOTHING;


-- ---------------------------------------------------------------------
-- DAILY SNAPSHOT — the history that makes trends possible.
--
-- cleaned_postings is overwritten on every run, so it only ever shows
-- the present. This table freezes one count per skill per day and never
-- updates those rows afterwards. A day not recorded is gone for good.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_daily_counts (
    snapshot_date  DATE NOT NULL,
    skill          TEXT NOT NULL,
    posting_count  INT  NOT NULL,
    PRIMARY KEY (snapshot_date, skill)
);

CREATE INDEX IF NOT EXISTS idx_skill_date ON skill_daily_counts (skill, snapshot_date);


-- ---------------------------------------------------------------------
-- snapshot_daily_skills() — record today's counts.
--
-- Counts postings seen TODAY only, so a listing that vanished last week
-- doesn't keep inflating current demand. Re-running on the same day
-- recalculates rather than duplicating.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION snapshot_daily_skills() RETURNS INT AS $$
DECLARE
    affected INT;
BEGIN
    INSERT INTO skill_daily_counts (snapshot_date, skill, posting_count)
    SELECT
        CURRENT_DATE,
        skill,
        COUNT(DISTINCT c.job_id)
    FROM cleaned_postings c, unnest(c.skills) AS skill
    WHERE c.last_seen_date = CURRENT_DATE
    GROUP BY skill
    ON CONFLICT (snapshot_date, skill) DO UPDATE
        SET posting_count = EXCLUDED.posting_count;

    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN affected;
END;
$$ LANGUAGE plpgsql;


-- =====================================================================
-- DELTA VIEWS — query these by name once history accumulates.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Day-over-day change.
-- Simple, but noisy at small scrape volumes: a skill can swing purely
-- because different postings happened to be on the search page.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW skill_delta_daily AS
SELECT
    s.skill,
    s.snapshot_date,
    s.posting_count,
    LAG(s.posting_count) OVER w AS previous_count,
    s.posting_count - LAG(s.posting_count) OVER w AS change,
    ROUND(
        100.0 * (s.posting_count - LAG(s.posting_count) OVER w)
        / NULLIF(LAG(s.posting_count) OVER w, 0), 1
    ) AS pct_change
FROM skill_daily_counts s
WHERE s.skill NOT IN (SELECT skill FROM skill_blocklist)
WINDOW w AS (PARTITION BY s.skill ORDER BY s.snapshot_date);


-- ---------------------------------------------------------------------
-- Change against a 7-day trailing average.
-- Asks "is today unusual for the past week?" rather than "is today
-- different from yesterday?" — far less sensitive to sampling noise.
--
-- The window EXCLUDES today (1 PRECEDING, not CURRENT ROW). Including
-- it would let a spike average against itself and understate its size.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW skill_delta_vs_baseline AS
WITH rolling AS (
    SELECT
        skill,
        snapshot_date,
        posting_count,
        AVG(posting_count) OVER (
            PARTITION BY skill
            ORDER BY snapshot_date
            ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
        ) AS baseline_7d
    FROM skill_daily_counts
    WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
)
SELECT
    skill,
    snapshot_date,
    posting_count,
    ROUND(baseline_7d, 1) AS baseline_7d,
    ROUND(posting_count - baseline_7d, 1) AS change_vs_baseline,
    ROUND(100.0 * (posting_count - baseline_7d) / NULLIF(baseline_7d, 0), 1) AS pct_vs_baseline
FROM rolling
WHERE baseline_7d IS NOT NULL;


-- ---------------------------------------------------------------------
-- First appearances — skills showing up for the very first time.
--
-- Needs its own view: the rolling-baseline view silently excludes these
-- (no history means no baseline), so a brand-new skill would never
-- surface there despite being the most interesting signal.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW skill_first_appearances AS
SELECT
    skill,
    MIN(snapshot_date) AS first_seen,
    COUNT(*)           AS days_present
FROM skill_daily_counts
WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
GROUP BY skill
ORDER BY first_seen DESC;


-- ---------------------------------------------------------------------
-- How much history exists so far. Run this to check whether the trend
-- views have enough data to say anything yet.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW snapshot_coverage AS
SELECT
    COUNT(DISTINCT snapshot_date) AS days_recorded,
    MIN(snapshot_date)            AS earliest,
    MAX(snapshot_date)            AS latest,
    COUNT(DISTINCT skill)         AS distinct_skills
FROM skill_daily_counts;
