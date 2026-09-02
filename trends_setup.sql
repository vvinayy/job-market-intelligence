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
    -- One series per source. Without this, adding a second job board would
    -- move every count on the day it arrived -- not because demand changed
    -- but because the population did -- and the snapshot cannot be recomputed
    -- afterwards to separate the two.
    source         TEXT NOT NULL DEFAULT 'naukri',
    posting_count  INT  NOT NULL,
    PRIMARY KEY (snapshot_date, skill, source)
);

CREATE INDEX IF NOT EXISTS idx_skill_date ON skill_daily_counts (skill, snapshot_date);

-- Existing installs predate `source`; both statements below are no-ops once
-- applied. Every row recorded before this ran came from Naukri and nothing
-- else, so the DEFAULT labels history truthfully rather than guessing at it.
ALTER TABLE skill_daily_counts
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'naukri';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'skill_daily_counts_pkey'
                  AND array_length(conkey, 1) = 2) THEN
        ALTER TABLE skill_daily_counts DROP CONSTRAINT skill_daily_counts_pkey;
        ALTER TABLE skill_daily_counts ADD PRIMARY KEY (snapshot_date, skill, source);
    END IF;
END $$;


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
    INSERT INTO skill_daily_counts (snapshot_date, skill, source, posting_count)
    SELECT
        CURRENT_DATE,
        sk.skill_name,
        -- Read the stored column, not the URL. This used to re-derive the
        -- rule here with its own CASE, which meant two copies of it: one
        -- here and one in cleaning.source_from_url(). A third job board
        -- would have had to be added to both, and a snapshot silently
        -- disagreeing with the postings table is not a mistake anything
        -- would surface.
        c.source,
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
    -- Positional: 2 is skill_name, 3 is c.source.
    GROUP BY 2, 3
    ON CONFLICT (snapshot_date, skill, source) DO UPDATE
        SET posting_count = EXCLUDED.posting_count;

    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN affected;
END;
$$ LANGUAGE plpgsql;


-- ---------------------------------------------------------------------
-- CORRECTIONS — a ledger over the snapshot, never an edit to it.
--
-- A recorded day cannot be recomputed: snapshot_daily_skills() reads
-- cleaned_postings, which only ever shows the present. So when a day
-- turns out to have been recorded from bad input, the raw row stays
-- exactly as observed and the adjustment is written here instead. The
-- observation and the judgement about it stay separable.
--
-- Why this exists: Naukri served an unrelated job description on up to
-- four postings a day over 2026-08-12..17, and the skills mined out of
-- it were snapshotted before anything could notice. C++ was recorded at
-- 9 on 12 Aug against a true 5, and at 3 on 14 Aug against a true 0 --
-- a ninefold spike and a week-long decay that never happened, sitting
-- in the one table this project cannot rebuild.
--
-- Deltas SUM, so one day can carry several corrections and each keeps
-- its own reason. Nothing is ever written here automatically: every row
-- is a deliberate entry with evidence behind it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_daily_corrections (
    correction_id SERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    skill         TEXT NOT NULL,
    -- A correction belongs to one source's series. The 24 rows written for
    -- 12-17 Aug all describe Naukri postings, so the DEFAULT is accurate.
    source        TEXT NOT NULL DEFAULT 'naukri',
    delta         INT  NOT NULL CHECK (delta <> 0),
    reason        TEXT NOT NULL,
    recorded_on   DATE NOT NULL DEFAULT CURRENT_DATE
);

ALTER TABLE skill_daily_corrections
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'naukri';

DROP INDEX IF EXISTS idx_skill_corrections;
CREATE INDEX IF NOT EXISTS idx_skill_corrections
    ON skill_daily_corrections (snapshot_date, skill, source);


-- ---------------------------------------------------------------------
-- INSTRUMENT CHANGES — dates when the measuring changed, not the market.
--
-- Distinct from skill_daily_corrections, and the distinction is the point.
-- A correction says an observation was WRONG and by how much. These rows say
-- both numbers were RIGHT and were taken with different instruments, so there
-- is no delta to record: nothing knows what the old days would have measured
-- under the new one, because cleaned_postings only ever shows the present.
--
-- Without this, a step shows up on /trends/movers as the largest change in
-- the dataset and reads as a hiring surge. The fourteen skills added on
-- 2026-09-02 recorded 0 every day before it -- not absent, invisible.
--
-- Nothing writes here automatically. A row is added by whoever changes the
-- instrument, the same discipline skill_daily_corrections depends on.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS instrument_changes (
    changed_on DATE NOT NULL,
    component  TEXT NOT NULL,
    summary    TEXT NOT NULL,
    PRIMARY KEY (changed_on, component)
);

INSERT INTO instrument_changes (changed_on, component, summary) VALUES
    ('2026-08-19', 'extraction',
     'Department, industry type, role category, Naukri role, applicant count, '
     'education and source_search began being collected. Coverage of those '
     'fields goes from roughly 20% to 100% across 18-19 Aug, so a "not stated" '
     'before this date means "not collected yet", not "the employer was silent".'),
    ('2026-09-02', 'skill_extraction',
     'extract_skills() rewritten to tokenise once instead of scanning per '
     'pattern, and 14 vocabulary entries added (Data Engineering, LLM, '
     'Generative AI, NoSQL, Big Data, Data Modeling, Data Ingestion, Data '
     'Pipeline, Data Integration, JPA, Hibernate, Distributed Systems, IICS, '
     'Matillion). 7.9 to 9.0 skills per description. The 14 recorded 0 every '
     'day before this because they were invisible, so they step from nothing.'),
    ('2026-09-02', 'source',
     'hirist.tech added as a second job board. skill_daily_counts.source keeps '
     'the series separate, so this affects cross-source totals only.')
ON CONFLICT (changed_on, component) DO NOTHING;


-- The three delta views read this rather than the raw table. Dropped
-- here first because they depend on it and it cannot be replaced while
-- they exist; each is recreated further down anyway.
DROP VIEW IF EXISTS skill_delta_daily;
DROP VIEW IF EXISTS skill_delta_vs_baseline;
DROP VIEW IF EXISTS skill_first_appearances;
DROP VIEW IF EXISTS skill_daily_counts_corrected;
DROP VIEW IF EXISTS skill_daily_counts_by_source;

-- Per-source series, corrections applied. Query this to compare platforms.
--
-- A skill corrected to zero drops out entirely, which is what the snapshot
-- itself would hold had the bad postings never been written: the table only
-- ever stores skills that actually appeared that day. raw_count and
-- correction stay visible so a reader can always see what was observed and
-- what was adjusted.
CREATE VIEW skill_daily_counts_by_source AS
SELECT
    s.snapshot_date,
    s.skill,
    s.source,
    s.posting_count + COALESCE(c.delta, 0) AS posting_count,
    s.posting_count                        AS raw_count,
    COALESCE(c.delta, 0)                   AS correction
FROM skill_daily_counts s
LEFT JOIN (
    SELECT snapshot_date, skill, source, SUM(delta) AS delta
    FROM skill_daily_corrections
    GROUP BY snapshot_date, skill, source
) c ON c.snapshot_date = s.snapshot_date
   AND c.skill = s.skill
   AND c.source = s.source
WHERE s.posting_count + COALESCE(c.delta, 0) > 0;


-- Every source combined, in exactly the shape the three delta views below
-- already expect. This is the seam: because they read a view rather than the
-- table, adding `source` underneath changed none of them, and with a single
-- source a SUM over one row returns that row unchanged.
CREATE VIEW skill_daily_counts_corrected AS
SELECT
    snapshot_date,
    skill,
    SUM(posting_count)::int AS posting_count,
    SUM(raw_count)::int     AS raw_count,
    SUM(correction)::int    AS correction
FROM skill_daily_counts_by_source
GROUP BY snapshot_date, skill;


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
FROM skill_daily_counts_corrected s
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
    FROM skill_daily_counts_corrected
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
FROM skill_daily_counts_corrected
WHERE skill NOT IN (SELECT skill FROM skill_blocklist)
GROUP BY skill
ORDER BY first_seen DESC;


-- ---------------------------------------------------------------------
-- How much history exists so far. Run this to check whether the trend
-- views have enough data to say anything yet.
--
-- Reads the RAW table, unlike the three views above: this answers "which
-- days were recorded", which a correction never changes. A day whose
-- counts were wrong was still a day we observed.
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
