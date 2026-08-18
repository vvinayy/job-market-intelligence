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
DROP TABLE IF EXISTS skills;
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
-- SKILLS — the dictionary. Each distinct skill name (after cleaning.py's
-- alias normalization) exists exactly once here, with its own id, so
-- posting_skills below can store a small integer instead of repeating
-- the same text on every row a skill appears in.
--
-- category is nullable and lives here, not in cleaning.py, so fixing or
-- adding a category is a data edit (UPDATE skills SET category = ...)
-- rather than a code change. job_database.py seeds it with an initial
-- guess from cleaning.py's SKILL_CATEGORIES when a skill is first seen
-- (NULL if that dict doesn't know it yet) and never overwrites it again
-- on later runs, so a manual correction here sticks.
-- ---------------------------------------------------------------------
CREATE TABLE skills (
    skill_id    SERIAL PRIMARY KEY,
    skill_name  TEXT NOT NULL UNIQUE,
    category    TEXT
);


-- ---------------------------------------------------------------------
-- POSTING_SKILLS — one row per posting, skill_ids as an array (both
-- sources merged — Naukri's own Key Skills chips and skills found by
-- scanning the description — deduplicated, resolved against the skills
-- dictionary). GIN-indexed so `&&` (any of) and `@>` (all of) filters
-- stay fast.
--
-- Postgres has no way to enforce a foreign key on individual array
-- elements, so referential integrity here is an application guarantee,
-- not a database one — job_database.py only ever writes skill_ids that
-- came from resolving a name through the skills dictionary. Analytics
-- that need one row per skill (demand, co-occurrence, suggestions,
-- the daily snapshot) unnest this array at query time instead of
-- reading it pre-exploded.
-- ---------------------------------------------------------------------
CREATE TABLE posting_skills (
    job_id     BIGINT NOT NULL PRIMARY KEY REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    skill_ids  INT[]  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_posting_skills_skill_ids ON posting_skills USING GIN (skill_ids);


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
