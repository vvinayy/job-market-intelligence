-- =====================================================================
-- CLEANING LAYER
-- Run this once to set up, then call clean_and_populate() after each
-- scrape.
--
--   psql -U postgres -d jobmarket -f cleaning_setup.sql
-- =====================================================================


-- ---------------------------------------------------------------------
-- SKILL ALIASES — the translation dictionary.
-- Lives in the database (not Python) so the cleaning step and anything
-- else querying the data both read from the same single source.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skill_aliases (
    alias           TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL
);

INSERT INTO skill_aliases (alias, canonical_name) VALUES
    ('power bi', 'Power BI'), ('powerbi', 'Power BI'),
    ('react', 'React'), ('reactjs', 'React'), ('react.js', 'React'),
    ('node', 'Node.js'), ('nodejs', 'Node.js'), ('node.js', 'Node.js'),
    ('postgres', 'PostgreSQL'), ('postgresql', 'PostgreSQL'),
    ('aws', 'AWS'), ('amazon web services', 'AWS'),
    ('gcp', 'GCP'), ('google cloud', 'GCP'), ('google cloud platform', 'GCP'),
    ('azure', 'Azure'), ('microsoft azure', 'Azure'),
    ('k8s', 'Kubernetes'), ('kubernetes', 'Kubernetes'),
    ('js', 'JavaScript'), ('javascript', 'JavaScript'),
    ('ts', 'TypeScript'), ('typescript', 'TypeScript'),
    ('ci/cd', 'CI/CD'), ('ci cd', 'CI/CD'),
    ('ml', 'Machine Learning'), ('machine learning', 'Machine Learning'),
    ('ai', 'AI'), ('artificial intelligence', 'AI'),
    ('sql', 'SQL'), ('nosql', 'NoSQL'),
    ('rest api', 'REST API'), ('restful api', 'REST API'),
    ('spark', 'Spark'), ('apache spark', 'Spark'), ('pyspark', 'Spark'),
    ('kafka', 'Kafka'), ('apache kafka', 'Kafka')
ON CONFLICT (alias) DO NOTHING;


-- ---------------------------------------------------------------------
-- normalize_skill() — one raw spelling in, agreed canonical name out.
-- Unknown skills fall through to a tidied version of themselves rather
-- than being dropped, so nothing is silently lost.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION normalize_skill(raw_skill TEXT) RETURNS TEXT AS $$
    SELECT COALESCE(
        (SELECT canonical_name FROM skill_aliases WHERE alias = lower(trim(raw_skill))),
        initcap(trim(raw_skill))
    );
$$ LANGUAGE sql IMMUTABLE;


-- ---------------------------------------------------------------------
-- parse_range() — pull two numbers out of strings like:
--     "6 - 10 years"   -> 6, 10
--     "15-25 Lacs P.A."-> 15, 25
--     "5+ years"       -> 5, NULL
-- Returns NULL,NULL when nothing numeric is present ("Not Disclosed").
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION parse_range_min(raw TEXT) RETURNS NUMERIC AS $$
    SELECT CASE
        WHEN raw IS NULL THEN NULL
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'))[1]::NUMERIC
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*\+'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*\+'))[1]::NUMERIC
        ELSE NULL
    END;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION parse_range_max(raw TEXT) RETURNS NUMERIC AS $$
    SELECT CASE
        WHEN raw IS NULL THEN NULL
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'))[2]::NUMERIC
        ELSE NULL   -- "5+ years" has no upper bound; don't invent one
    END;
$$ LANGUAGE sql IMMUTABLE;


-- ---------------------------------------------------------------------
-- CLEANED TABLE — what the API and interface will actually read from.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cleaned_postings (
    job_id           BIGINT PRIMARY KEY REFERENCES raw_postings(job_id),
    url              TEXT,
    title            TEXT,
    company          TEXT,
    locations        TEXT[],          -- split from "Hyderabad, Chennai, Bengaluru"
    experience_min   INT,
    experience_max   INT,
    salary_min       NUMERIC,         -- in Lacs P.A.; NULL when not disclosed
    salary_max       NUMERIC,
    key_skills       TEXT[],          -- canonical names
    tech_in_desc     TEXT[],          -- canonical names
    employment_type  TEXT,
    posted_date      DATE,
    openings         INT,
    first_seen_date  DATE,
    last_seen_date   DATE,
    times_seen       INT
);


-- ---------------------------------------------------------------------
-- clean_and_populate() — read raw_postings, write the cleaned version.
-- Safe to run repeatedly: existing rows are updated, not duplicated.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION clean_and_populate() RETURNS INT AS $$
DECLARE
    affected INT;
BEGIN
    INSERT INTO cleaned_postings (
        job_id, url, title, company, locations,
        experience_min, experience_max, salary_min, salary_max,
        key_skills, tech_in_desc, employment_type,
        posted_date, openings, first_seen_date, last_seen_date, times_seen
    )
    SELECT
        r.job_id,
        r.url,
        r.title,
        r.company,

        -- "Hyderabad, Chennai, Bengaluru" -> {Hyderabad,Chennai,Bengaluru}
        CASE WHEN r.location IS NULL THEN NULL
             ELSE ARRAY(SELECT trim(x) FROM unnest(string_to_array(r.location, ',')) AS x
                        WHERE trim(x) <> '')
        END,

        parse_range_min(r.experience)::INT,
        parse_range_max(r.experience)::INT,

        -- "Not Disclosed" has no digits, so both come back NULL
        parse_range_min(r.salary),
        parse_range_max(r.salary),

        -- Take each skill apart, normalize it, drop duplicates, reassemble
        CASE WHEN r.key_skills IS NULL THEN NULL
             ELSE ARRAY(SELECT DISTINCT normalize_skill(s) FROM unnest(r.key_skills) AS s)
        END,
        CASE WHEN r.tech_in_desc IS NULL THEN NULL
             ELSE ARRAY(SELECT DISTINCT normalize_skill(s) FROM unnest(r.tech_in_desc) AS s)
        END,

        r.employment_type,
        r.posted_date,
        r.openings,
        r.first_seen_date,
        r.last_seen_date,
        r.times_seen
    FROM raw_postings r
    ON CONFLICT (job_id) DO UPDATE SET
        locations       = EXCLUDED.locations,
        experience_min  = EXCLUDED.experience_min,
        experience_max  = EXCLUDED.experience_max,
        salary_min      = EXCLUDED.salary_min,
        salary_max      = EXCLUDED.salary_max,
        key_skills      = EXCLUDED.key_skills,
        tech_in_desc    = EXCLUDED.tech_in_desc,
        employment_type = EXCLUDED.employment_type,
        posted_date     = EXCLUDED.posted_date,
        openings        = EXCLUDED.openings,
        last_seen_date  = EXCLUDED.last_seen_date,
        times_seen      = EXCLUDED.times_seen;

    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN affected;
END;
$$ LANGUAGE plpgsql;
