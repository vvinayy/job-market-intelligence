-- =====================================================================
-- TREND LAYER
--
-- Run once to set up:
--   psql -U postgres -d jobmarket -f trends_setup.sql
--
-- Then, once per day AFTER the scrape completes:
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
    ('Workflow'), ('Scheduling'), ('Analytical'), ('It Services'),
    -- Extraction noise: fragments that are ordinary English words, not
    -- skills. Harmless in a count (1-5 postings each) but actively bad
    -- for skill_groups, which scans description prose -- "As", "Be",
    -- "Do", "Min" and "Pre" match constantly inside normal sentences.
    -- 'C', 'R' and 'Go' are NOT here on purpose: those are real
    -- languages despite being just as short.
    ('S'), ('As'), ('Be'), ('Do'), ('Ap'), ('Cg'), ('3m'),
    ('L1'), ('L2'), ('Min'), ('Pre'), ('Data'),
    -- Too generic to be an alternative to anything: a posting saying
    -- "AWS or cloud services" is not offering a choice between them.
    ('Cloud Services')
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
        sk.skill_name,
        COUNT(DISTINCT c.job_id)
    FROM cleaned_postings c
    JOIN posting_skills ps ON ps.job_id = c.job_id
    -- skill_ids holds only the outright requirements; a skill offered
    -- as one of several alternatives lives in skill_groups and is not
    -- repeated there. Demand means "this posting would accept AWS",
    -- so both are counted -- reading skill_ids alone would have cut
    -- AWS from 207 postings to 149 overnight and made every day after
    -- today incomparable with every day before it.
    JOIN LATERAL unnest(ps.skill_ids || skill_group_ids(ps.skill_groups)) AS u(skill_id) ON true
    JOIN skills sk ON sk.skill_id = u.skill_id
    WHERE c.last_seen_date = CURRENT_DATE
    GROUP BY sk.skill_name
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
-- Change since the previous snapshot.
--
-- NOT day-over-day, and that is why previous_date and
-- days_since_previous are here. Two separate gaps stack up.
--
-- Snapshots only happen when the machine is on -- 17 days recorded
-- across 25 calendar days, five gaps, the largest three. On top of
-- that, a skill absent from a snapshot has no row that day at all, so
-- LAG reaches back to whenever it last appeared. Measured on 31 Aug:
-- 204 skills had a genuine one-day comparison, 121 spanned two days,
-- and a tail ran to 4, 6, 14 and 24 days.
--
-- So the interval is NOT shared across skills on a date, and a 24-day
-- change ranked against a one-day change is not a fair comparison.
-- Consumers must filter on days_since_previous; /trends/movers does.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS skill_delta_daily;
CREATE VIEW skill_delta_daily AS
SELECT
    s.skill,
    s.snapshot_date,
    s.posting_count,
    LAG(s.posting_count) OVER w AS previous_count,
    LAG(s.snapshot_date)  OVER w AS previous_date,
    (s.snapshot_date - LAG(s.snapshot_date) OVER w)::int AS days_since_previous,
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
-- different from yesterday?" -- far less sensitive to sampling noise.
--
-- RANGE over an interval, not ROWS. ROWS BETWEEN 7 PRECEDING averages
-- the last seven recorded snapshots, which is only a week if every day
-- was recorded; with the real gaps that window spanned 20-31 Aug, so an
-- eleven-day average was being reported as a seven-day one. RANGE walks
-- calendar dates instead and simply averages fewer points across a gap.
--
-- baseline_days says how many points that was. A baseline built from
-- two snapshots is not the same claim as one built from seven, and
-- without this column the two are indistinguishable.
--
-- The window EXCLUDES today (1 day PRECEDING, not CURRENT ROW).
-- Including it would let a spike average against itself and understate
-- its size.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS skill_delta_vs_baseline;
CREATE VIEW skill_delta_vs_baseline AS
WITH rolling AS (
    SELECT
        skill,
        snapshot_date,
        posting_count,
        AVG(posting_count) OVER w AS baseline_7d,
        COUNT(*)           OVER w AS baseline_days
    FROM skill_daily_counts
    WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
    WINDOW w AS (
        PARTITION BY skill
        ORDER BY snapshot_date
        RANGE BETWEEN INTERVAL '7 days' PRECEDING
                  AND INTERVAL '1 day'  PRECEDING
    )
)
SELECT
    skill,
    snapshot_date,
    posting_count,
    ROUND(baseline_7d, 1) AS baseline_7d,
    baseline_days::int    AS baseline_days,
    ROUND(posting_count - baseline_7d, 1) AS change_vs_baseline,
    ROUND(100.0 * (posting_count - baseline_7d) / NULLIF(baseline_7d, 0), 1) AS pct_vs_baseline
FROM rolling
WHERE baseline_7d IS NOT NULL;


-- ---------------------------------------------------------------------
-- First appearances -- skills showing up for the very first time.
--
-- Needs its own view: the rolling-baseline view silently excludes these
-- (no history means no baseline), so a brand-new skill would never
-- surface there despite being the most interesting signal.
--
-- days_present counts snapshots, NOT elapsed days, and the two diverge
-- badly across gaps: 740 skills have days_present <= 7 while being more
-- than a week old, some by 24 days. Filtering on it to mean "new this
-- week" is wrong, which is what the weekly digest was doing.
-- days_since_first_seen is the calendar age and is what that question
-- actually wants; days_present stays because "seen on 5 of 17 days" is
-- a different and useful fact.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS skill_first_appearances;
CREATE VIEW skill_first_appearances AS
SELECT
    skill,
    MIN(snapshot_date)                       AS first_seen,
    COUNT(*)                                 AS days_present,
    (CURRENT_DATE - MIN(snapshot_date))::int AS days_since_first_seen
FROM skill_daily_counts
WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
GROUP BY skill
ORDER BY first_seen DESC;


-- ---------------------------------------------------------------------
-- How much history exists so far. Run this to check whether the trend
-- views have enough data to say anything yet.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS snapshot_coverage;
CREATE VIEW snapshot_coverage AS
SELECT
    COUNT(DISTINCT snapshot_date) AS days_recorded,
    MIN(snapshot_date)            AS earliest,
    MAX(snapshot_date)            AS latest,
    COUNT(DISTINCT skill)         AS distinct_skills,
    -- days_recorded alone reads as an unbroken run. It is not: snapshots
    -- only happen when the machine is on. The difference between these
    -- two is how many days are missing, permanently.
    (MAX(snapshot_date) - MIN(snapshot_date) + 1)::int AS calendar_days_spanned
FROM skill_daily_counts;
