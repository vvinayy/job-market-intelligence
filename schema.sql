-- =====================================================================
-- SCHEMA — every table in the pipeline: cities/states, cleaned_postings,
-- posting_cities, posting_skills, posting_qualifications.
--
-- Run order: this file, THEN trends_setup.sql.
--
-- There is deliberately no raw_postings table. The scraper cleans each
-- posting in-process (job_database.py, calling into cleaning.py) and
-- writes straight into cleaned_postings — nothing scraped is ever
-- stored unprocessed. cleaning.py holds every transformation function;
-- this file only defines table shapes.
--
-- Run once:
--   psql -U postgres -d jobmarket -f schema.sql
--   (or open in pgAdmin Query Tool and run the whole file)
-- =====================================================================


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
-- CLEANED POSTINGS — the only postings table. Written by cleaning.py
-- (called in-process by the scraper right after each posting is
-- scraped), read by the API.
--
-- fingerprint drives dedup directly on this table (company + title +
-- location + experience, hashed) — a repeat sighting updates the row
-- in place instead of inserting a duplicate; see job_database.py.
--
-- skills and qualifications live in their own tables (posting_skills,
-- posting_qualifications) rather than array columns, since both need
-- a second attribute per entry (category; level) that a bare array
-- has nowhere to hold. city_ids stays an array here for an at-a-glance
-- read — posting_cities holds the same links for joining/filtering.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS posting_qualifications;
DROP TABLE IF EXISTS posting_skills;
DROP TABLE IF EXISTS posting_cities;
DROP TABLE IF EXISTS cleaned_postings;

CREATE TABLE cleaned_postings (
    job_id             BIGSERIAL PRIMARY KEY,
    fingerprint        TEXT NOT NULL UNIQUE,
    url                TEXT NOT NULL,
    title              TEXT,
    company            TEXT,
    description        TEXT,
    experience_min     INT,
    experience_max     INT,
    salary_min         NUMERIC,
    salary_max         NUMERIC,
    city_ids           INT[],
    unmapped_locations TEXT[],
    working_type       TEXT,
    employment_type    TEXT,
    contract_type      TEXT,
    role_family        TEXT,
    role_category      TEXT,
    posted_date        DATE,
    posted_raw         TEXT,
    openings           INT,
    first_seen_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen_date     DATE NOT NULL DEFAULT CURRENT_DATE,
    times_seen         INT NOT NULL DEFAULT 1
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


-- ---------------------------------------------------------------------
-- POSTING_SKILLS — one row per skill per posting, both sources merged
-- (Naukri's own Key Skills chips and skills found by scanning the
-- description) and deduplicated. category comes from a lookup dict in
-- cleaning.py (Frontend / Backend / Database / Cloud-DevOps / Data-ML /
-- etc.) — same role SKILL_ALIASES already plays for spelling, just one
-- more attribute per skill, which is exactly what a bare array column
-- had no room for.
-- ---------------------------------------------------------------------
CREATE TABLE posting_skills (
    job_id    BIGINT NOT NULL REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    skill     TEXT   NOT NULL,
    category  TEXT,
    PRIMARY KEY (job_id, skill)
);

CREATE INDEX IF NOT EXISTS idx_posting_skills_skill ON posting_skills (skill);


-- ---------------------------------------------------------------------
-- POSTING_QUALIFICATIONS — one row per UG/PG/Doctorate entry actually
-- present on a posting (a posting with no Doctorate row simply gets no
-- row here for it, rather than a padded NULL). field_of_study holds
-- the free text Naukri shows ("Any Graduate", "B.Tech/B.E. in Any
-- Specialization").
-- ---------------------------------------------------------------------
CREATE TABLE posting_qualifications (
    job_id          BIGINT NOT NULL REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    level           TEXT   NOT NULL,
    field_of_study  TEXT,
    PRIMARY KEY (job_id, level)
);
