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
    job_id                 BIGSERIAL PRIMARY KEY,
    fingerprint            TEXT NOT NULL UNIQUE,
    url                    TEXT NOT NULL,
    title                  TEXT,
    company                TEXT,
    description            TEXT,
    -- Exact-match hash of the normalized description text — not fuzzy —
    -- so postings sharing verbatim JD text (common when several
    -- staffing agencies repost the same vacancy) can be grouped:
    -- SELECT description_hash FROM cleaned_postings
    --   GROUP BY description_hash HAVING COUNT(DISTINCT company) > 1
    description_hash       TEXT,
    -- Best-effort split of `description` by heading phrases
    -- ("Roles and Responsibilities" / "Desired Candidate Profile" etc.)
    -- — text heuristics, not guaranteed for every posting's phrasing.
    responsibilities_text  TEXT,
    requirements_text      TEXT,
    experience_min         INT,
    experience_max         INT,
    salary_min              NUMERIC,
    salary_max              NUMERIC,
    city_ids                INT[],
    unmapped_locations      TEXT[],
    working_type            TEXT,
    employment_type         TEXT,
    contract_type           TEXT,
    role_family             TEXT,
    -- Inferred from the title alone, not experience_min/max — a
    -- different, often-absent signal. NULL means the title carried no
    -- seniority marker at all (the common case), not "mid-level".
    seniority_level          TEXT,
    role_category           TEXT,
    -- Naukri's own classification (e.g. "Back End Developer") — distinct
    -- from role_category (e.g. "Software Development") and from
    -- role_family (our own regex-derived classification of the title).
    naukri_role              TEXT,
    industry_type            TEXT,
    department               TEXT,
    posted_date               DATE,
    posted_raw                TEXT,
    openings                  INT,
    -- Naukri shows this three ways: a plain exact number ("44"), a
    -- floor once past some threshold ("100+"), or a ceiling for very
    -- new postings ("Less than 10"). applicant_count is always the
    -- digit found either way; applicant_count_qualifier records which
    -- direction the ambiguity runs — NULL means the number was exact,
    -- not that the qualifier is unknown. 'at_least'/'less_than' point
    -- opposite directions, so collapsing both into a bare int the way
    -- an earlier version of this column did would be actively wrong
    -- for the 'less_than' case, not just imprecise.
    applicant_count           INT,
    applicant_count_qualifier TEXT,
    -- Shown inline in the posting header, sourced from AmbitionBox — no
    -- separate company-profile page visit needed. company_reviews is
    -- Naukri's own rounded figure ("50.5K Reviews") expanded from its
    -- K/M shorthand, not a more precise count than the source has.
    company_rating             NUMERIC,
    company_reviews            INT,
    -- Recognition badges from the inline "About the company" block
    -- (e.g. "Fortune India 500 (2023)", "Highly Rated by Women").
    -- Confirmed from raw HTML that short entries like "TOP" are
    -- genuinely what Naukri shows, not a truncation artifact.
    company_badges             TEXT[],
    -- Which run_daily_scrape.bat search URL surfaced this posting —
    -- lets a query answer "which searches are actually productive"
    -- instead of only ever seeing the merged result.
    source_search             TEXT,
    -- Mined from description text via skill_taxonomy.extract_certifications()
    -- — a credential someone holds, a different kind of signal from a
    -- tool/language skill, kept separate from posting_skills rather
    -- than folded in.
    certifications            TEXT[],
    first_seen_date          DATE NOT NULL DEFAULT CURRENT_DATE,
    last_seen_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    times_seen                INT NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cleaned_postings_description_hash
    ON cleaned_postings (description_hash);


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
-- preferred_skill_ids: whichever of skill_ids Naukri starred as
-- "preferred" on the page (a real distinction, confirmed from the
-- page's own legend and its <i class="ni-icon-jd-save"> marker) —
-- always a subset of skill_ids, enforced by the CHECK below rather
-- than just by convention in job_database.py.
CREATE TABLE posting_skills (
    job_id                BIGINT NOT NULL PRIMARY KEY REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    skill_ids             INT[]  NOT NULL DEFAULT '{}',
    preferred_skill_ids    INT[]  NOT NULL DEFAULT '{}',
    CONSTRAINT preferred_skills_subset_of_skills CHECK (preferred_skill_ids <@ skill_ids)
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


-- ---------------------------------------------------------------------
-- SCRAPE_RUNS — one row per naukri_collector.py invocation. Not job
-- data — this is what makes a broken selector visible instead of
-- silent. field_found_counts is JSONB (not a fixed set of columns)
-- because the set of scraped fields changes as the pipeline grows;
-- job_database.py's check_field_health() unpivots it against recent
-- runs to catch a field whose found-rate suddenly drops, which is
-- exactly what happened with Department/Industry Type and the
-- applicant-count bug earlier in this project — both would have shown
-- up here immediately instead of being caught by hand.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id              BIGSERIAL PRIMARY KEY,
    search_url          TEXT NOT NULL,
    started_at          TIMESTAMP NOT NULL,
    finished_at         TIMESTAMP,
    postings_found      INT,
    postings_scraped    INT,
    postings_written    INT,
    field_found_counts  JSONB,
    -- False when save_records() raised and the run fell back to the
    -- JSON dump — a real "storage isn't working" signal, not just a
    -- slow/missing-field one.
    storage_ok          BOOLEAN NOT NULL DEFAULT true,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_started_at ON scrape_runs (started_at);
