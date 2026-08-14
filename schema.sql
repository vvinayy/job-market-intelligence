-- =====================================================================
-- SCHEMA — every table in the pipeline: raw_postings, cities/states,
-- cleaned_postings, posting_cities.
--
-- Run order: this file, THEN trends_setup.sql.
--
-- The cleaning logic that used to live in separate SQL files
-- (clean_and_populate() and its helper functions, plus the
-- city_aliases/skill_aliases/role_patterns lookup tables that only
-- those functions used) now lives in cleaning.py. This file only
-- defines table shapes — raw data in, cleaned data out. The actual
-- transformation happens in Python, not in Postgres.
--
-- Run once:
--   psql -U postgres -d jobmarket -f schema.sql
--   (or open in pgAdmin Query Tool and run the whole file)
--
-- Every CREATE is IF NOT EXISTS or preceded by its own DROP, so this
-- is safe to run against a database that already has some of these
-- tables — raw_postings is never dropped; cleaned_postings and
-- posting_cities are rebuilt, which is fine since everything in them
-- is reproducible from raw_postings by re-running `python cleaning.py`.
-- =====================================================================


-- ---------------------------------------------------------------------
-- RAW_POSTINGS — one row per scraped job posting, exactly as read off
-- the page. Never transformed; cleaning.py reads from this, nothing
-- writes to it except the scraper (job_database.py).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_postings (
    job_id           BIGSERIAL PRIMARY KEY,
    url              TEXT NOT NULL,
    fingerprint      TEXT NOT NULL UNIQUE,
    title            TEXT,
    company          TEXT,
    experience       TEXT,
    location         TEXT,
    key_skills       TEXT[],
    tech_in_desc     TEXT[],
    employment_type  TEXT,
    working_type     TEXT,
    salary           TEXT,
    description      TEXT,
    posted_date      DATE,
    posted_raw       TEXT,
    openings         INTEGER,
    first_seen_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    times_seen       INTEGER NOT NULL DEFAULT 1
);


-- ---------------------------------------------------------------------
-- STATES — stored once, referenced by cities.
-- state_name is UNIQUE so it can serve as the foreign key target,
-- which lets cities show the readable name directly instead of an id.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS states (
    state_id    SERIAL PRIMARY KEY,
    state_name  TEXT NOT NULL UNIQUE
);

INSERT INTO states (state_name) VALUES
    ('Karnataka'), ('Telangana'), ('Maharashtra'), ('Tamil Nadu'),
    ('Delhi'), ('Haryana'), ('Uttar Pradesh'), ('West Bengal'),
    ('Gujarat'), ('Kerala'), ('Rajasthan'), ('Madhya Pradesh'),
    ('Chandigarh'), ('Odisha'), ('Andhra Pradesh'), ('Multi-state')
ON CONFLICT (state_name) DO NOTHING;


-- ---------------------------------------------------------------------
-- CITIES — the canonical list. Queried directly by the API
-- (/reference/cities, /reference/states) and by cleaning.py, which
-- builds its own city-name -> city_id lookup from this table at the
-- start of each run.
--
-- `state` is a foreign key pointing at states(state_name), so the name
-- is readable here directly while still being validated: you cannot
-- insert a city with a state that isn't in the states table.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS cities CASCADE;

CREATE TABLE cities (
    city_id     SERIAL PRIMARY KEY,
    city_name   TEXT NOT NULL UNIQUE,
    state       TEXT NOT NULL REFERENCES states(state_name)
);

INSERT INTO cities (city_name, state) VALUES
    ('Bengaluru',           'Karnataka'),
    ('Hyderabad',           'Telangana'),
    ('Secunderabad',        'Telangana'),
    ('Nizamabad',           'Telangana'),
    ('Warangal',            'Telangana'),
    ('Pune',                'Maharashtra'),
    ('Mumbai',              'Maharashtra'),
    ('Chennai',             'Tamil Nadu'),
    ('Coimbatore',          'Tamil Nadu'),
    ('Delhi',               'Delhi'),
    ('Delhi / NCR',         'Multi-state'),   -- region label, not a single city
    ('Gurugram',            'Haryana'),
    ('Faridabad',           'Haryana'),
    ('Noida',               'Uttar Pradesh'),
    ('Greater Noida',       'Uttar Pradesh'),
    ('Ghaziabad',           'Uttar Pradesh'),
    ('Kolkata',             'West Bengal'),
    ('Ahmedabad',           'Gujarat'),
    ('Kochi',               'Kerala'),
    ('Thiruvananthapuram',  'Kerala'),
    ('Jaipur',              'Rajasthan'),
    ('Indore',              'Madhya Pradesh'),
    ('Chandigarh',          'Chandigarh'),
    ('Bhubaneswar',         'Odisha'),
    ('Visakhapatnam',       'Andhra Pradesh')
ON CONFLICT (city_name) DO NOTHING;


-- ---------------------------------------------------------------------
-- CLEANED POSTINGS — written by cleaning.py, read by the API.
--   * skills: ONE merged, deduplicated column
--   * working_type / employment_type / contract_type: three fields
--   * unmapped_locations: location text that matched no known city, so
--     gaps in the city-alias mapping (in cleaning.py) are visible
--     rather than silent
--   * role_family: the classify_role() output
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS posting_cities;
DROP TABLE IF EXISTS cleaned_postings;

CREATE TABLE cleaned_postings (
    job_id             BIGINT PRIMARY KEY REFERENCES raw_postings(job_id),
    url                TEXT,
    title              TEXT,
    company            TEXT,
    experience_min     INT,
    experience_max     INT,
    salary_min         NUMERIC,
    salary_max         NUMERIC,
    skills             TEXT[],
    city_ids           INT[],           -- readable at a glance; posting_cities
                                        -- holds the same links for joining
    working_type       TEXT,
    employment_type    TEXT,
    contract_type      TEXT,
    unmapped_locations TEXT[],
    posted_date        DATE,
    openings           INT,
    first_seen_date    DATE,
    last_seen_date     DATE,
    times_seen         INT,
    role_family        TEXT
);


-- ---------------------------------------------------------------------
-- POSTING_CITIES — the many-to-many junction.
-- One posting listing three cities produces three rows here.
-- ---------------------------------------------------------------------
CREATE TABLE posting_cities (
    job_id   BIGINT NOT NULL REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    city_id  INT    NOT NULL REFERENCES cities(city_id),
    PRIMARY KEY (job_id, city_id)
);

CREATE INDEX IF NOT EXISTS idx_posting_cities_city ON posting_cities (city_id);
