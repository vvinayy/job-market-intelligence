-- =====================================================================
-- SCHEMA v2 — cities, states, merged skills, split employment fields
--
-- REQUIRES cleaning_setup.sql to have already run: the clean_and_populate()
-- function below calls normalize_skill() and parse_range_min/max(), which
-- are defined there, not here. Run order: schema.sql, cleaning_setup.sql,
-- THEN this file, then roles_setup.sql, then trends_setup.sql.
--
-- Run once:
--   psql -U postgres -d jobmarket -f schema_v2.sql
--   (or open in pgAdmin Query Tool and run the whole file)
--
-- raw_postings is NOT dropped — only a column is added to it.
-- cleaned_postings IS rebuilt, since its shape changes. Everything in
-- it is reproducible from raw_postings by re-running clean_and_populate().
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
-- CITIES — the canonical list.
--
-- `state` is a foreign key pointing at states(state_name), so the name
-- is readable here directly while still being validated: you cannot
-- insert a city with a state that isn't in the states table. The name
-- is only stored authoritatively in one place, so there is nothing to
-- drift out of sync.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS city_aliases;
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
-- CITY ALIASES — every spelling Naukri uses, mapped to one city.
-- Aliases are stored lowercase; lookups lowercase the input to match.
-- ---------------------------------------------------------------------
CREATE TABLE city_aliases (
    alias    TEXT PRIMARY KEY,
    city_id  INT NOT NULL REFERENCES cities(city_id)
);

INSERT INTO city_aliases (alias, city_id)
SELECT v.alias, c.city_id
FROM (VALUES
    -- Bengaluru (Bangalore Rural folds in, per decision)
    ('bengaluru', 'Bengaluru'), ('bangalore', 'Bengaluru'),
    ('bangalore rural', 'Bengaluru'), ('bengaluru rural', 'Bengaluru'),
    -- Mumbai — Naukri lists "Mumbai" and "Mumbai (All Areas)" separately
    ('mumbai', 'Mumbai'), ('mumbai (all areas)', 'Mumbai'),
    ('navi mumbai', 'Mumbai'), ('thane', 'Mumbai'),
    -- Delhi vs the NCR region label — kept distinct on purpose
    ('delhi', 'Delhi'), ('new delhi', 'Delhi'),
    ('delhi / ncr', 'Delhi / NCR'), ('delhi/ncr', 'Delhi / NCR'),
    ('ncr', 'Delhi / NCR'),
    -- Gurugram (current name) with the older spelling as alias
    ('gurugram', 'Gurugram'), ('gurgaon', 'Gurugram'),
    -- Noida and Greater Noida kept separate
    ('noida', 'Noida'), ('greater noida', 'Greater Noida'),
    -- Hyderabad and Secunderabad kept separate
    ('hyderabad', 'Hyderabad'), ('secunderabad', 'Secunderabad'),
    -- Kochi
    ('kochi', 'Kochi'), ('cochin', 'Kochi'), ('ernakulam', 'Kochi'),
    -- Thiruvananthapuram
    ('thiruvananthapuram', 'Thiruvananthapuram'), ('trivandrum', 'Thiruvananthapuram'),
    -- Visakhapatnam
    ('visakhapatnam', 'Visakhapatnam'), ('vizag', 'Visakhapatnam'),
    -- Straightforward one-to-one
    ('pune', 'Pune'), ('chennai', 'Chennai'), ('coimbatore', 'Coimbatore'),
    ('kolkata', 'Kolkata'), ('ahmedabad', 'Ahmedabad'), ('jaipur', 'Jaipur'),
    ('indore', 'Indore'), ('chandigarh', 'Chandigarh'),
    ('bhubaneswar', 'Bhubaneswar'), ('nizamabad', 'Nizamabad'),
    ('warangal', 'Warangal'), ('faridabad', 'Faridabad'),
    ('ghaziabad', 'Ghaziabad')
) AS v(alias, city)
JOIN cities c ON c.city_name = v.city
ON CONFLICT (alias) DO NOTHING;


-- ---------------------------------------------------------------------
-- Normalisation helpers for the employment fields.
--
-- Naukri gives ONE string for employment ("Full Time, Permanent") and a
-- SEPARATE field for work mode ("Hybrid"). The two functions below split
-- that combined string by matching each comma-separated part against a
-- known vocabulary — not by position, so "Permanent, Full Time" and a
-- bare "Full Time" both land correctly. Anything unrecognised -> NULL.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION parse_employment_type(raw TEXT) RETURNS TEXT AS $$
    SELECT part FROM (
        SELECT initcap(trim(p)) AS part
        FROM unnest(string_to_array(COALESCE(raw, ''), ',')) AS p
    ) x
    WHERE lower(part) IN ('full time', 'full-time', 'part time', 'part-time')
    LIMIT 1;
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION parse_contract_type(raw TEXT) RETURNS TEXT AS $$
    SELECT part FROM (
        SELECT initcap(trim(p)) AS part
        FROM unnest(string_to_array(COALESCE(raw, ''), ',')) AS p
    ) x
    WHERE lower(part) IN ('permanent', 'contract', 'contractual', 'temporary', 'internship', 'freelance')
    LIMIT 1;
$$ LANGUAGE sql IMMUTABLE;

-- Naukri only renders the work-mode badge for Hybrid/Remote/WFH postings;
-- an on-site posting gets no badge at all. So a NULL/unrecognised raw
-- value isn't a missing observation, it's Naukri's own way of saying
-- "on-site" — treating it as unknown was undercounting On-site by
-- roughly 3 postings out of 4.
CREATE OR REPLACE FUNCTION normalize_working_type(raw TEXT) RETURNS TEXT AS $$
    SELECT CASE
        WHEN lower(raw) LIKE '%hybrid%' THEN 'Hybrid'
        WHEN lower(raw) LIKE '%remote%' THEN 'Remote'
        WHEN lower(raw) LIKE '%work from home%' THEN 'Remote'
        WHEN lower(raw) LIKE '%office%' THEN 'On-site'
        ELSE 'On-site'
    END;
$$ LANGUAGE sql IMMUTABLE;


-- ---------------------------------------------------------------------
-- raw_postings gains working_type. Existing rows get NULL, which is
-- accurate — they were scraped before the field was collected.
-- ---------------------------------------------------------------------
ALTER TABLE raw_postings ADD COLUMN IF NOT EXISTS working_type TEXT;


-- ---------------------------------------------------------------------
-- CLEANED POSTINGS — rebuilt.
--   * skills: ONE merged, deduplicated column
--   * working_type / employment_type / contract_type: three fields
--   * unmapped_locations: location text that matched no known city, so
--     gaps in the alias table are visible rather than silent
--   * locations array is gone — cities now live in posting_cities
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
    times_seen         INT
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
-- clean_and_populate() — rebuilt for the new shape.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION clean_and_populate() RETURNS INT AS $$
DECLARE
    affected INT;
BEGIN
    INSERT INTO cleaned_postings (
        job_id, url, title, company,
        experience_min, experience_max, salary_min, salary_max,
        skills, city_ids, working_type, employment_type, contract_type,
        unmapped_locations, posted_date, openings,
        first_seen_date, last_seen_date, times_seen, role_family
    )
    SELECT
        r.job_id, r.url, r.title, r.company,
        parse_range_min(r.experience)::INT,
        parse_range_max(r.experience)::INT,
        parse_range_min(r.salary),
        parse_range_max(r.salary),

        -- Merge both skill sources, normalise each, drop duplicates.
        -- normalize_skill() is what makes "Power Bi" and "Power BI"
        -- collapse into one entry rather than two.
        ARRAY(
            SELECT DISTINCT normalize_skill(s)
            FROM unnest(
                COALESCE(r.key_skills, '{}') || COALESCE(r.tech_in_desc, '{}')
            ) AS s
            WHERE trim(s) <> ''
        ),

        -- City ids for this posting, resolved through the alias table.
        ARRAY(
            SELECT DISTINCT a.city_id
            FROM unnest(string_to_array(COALESCE(r.location, ''), ',')) AS loc
            JOIN city_aliases a ON a.alias = lower(trim(loc))
        ),

        normalize_working_type(r.working_type),
        parse_employment_type(r.employment_type),
        parse_contract_type(r.employment_type),

        -- Location fragments with no alias match. Visible, not dropped.
        ARRAY(
            SELECT trim(loc)
            FROM unnest(string_to_array(COALESCE(r.location, ''), ',')) AS loc
            WHERE trim(loc) <> ''
              AND lower(trim(loc)) <> 'india'          -- not a city
              AND lower(trim(loc)) NOT LIKE '%remote%' -- a work mode
              AND NOT EXISTS (
                  SELECT 1 FROM city_aliases a WHERE a.alias = lower(trim(loc))
              )
        ),

        r.posted_date, r.openings,
        r.first_seen_date, r.last_seen_date, r.times_seen,
        classify_role(r.title)
    FROM raw_postings r
    ON CONFLICT (job_id) DO UPDATE SET
        experience_min     = EXCLUDED.experience_min,
        experience_max     = EXCLUDED.experience_max,
        salary_min         = EXCLUDED.salary_min,
        salary_max         = EXCLUDED.salary_max,
        skills             = EXCLUDED.skills,
        city_ids           = EXCLUDED.city_ids,
        working_type       = EXCLUDED.working_type,
        employment_type    = EXCLUDED.employment_type,
        contract_type      = EXCLUDED.contract_type,
        unmapped_locations = EXCLUDED.unmapped_locations,
        posted_date        = EXCLUDED.posted_date,
        openings           = EXCLUDED.openings,
        last_seen_date     = EXCLUDED.last_seen_date,
        times_seen         = EXCLUDED.times_seen,
        role_family        = EXCLUDED.role_family;

    GET DIAGNOSTICS affected = ROW_COUNT;

    -- Rebuild the city links. Cheap at this scale, and avoids stale
    -- rows when a posting's location changes between scrapes.
    DELETE FROM posting_cities;

    INSERT INTO posting_cities (job_id, city_id)
    SELECT DISTINCT r.job_id, a.city_id
    FROM raw_postings r,
         unnest(string_to_array(COALESCE(r.location, ''), ',')) AS loc
    JOIN city_aliases a ON a.alias = lower(trim(loc))
    WHERE EXISTS (SELECT 1 FROM cleaned_postings c WHERE c.job_id = r.job_id)
    ON CONFLICT DO NOTHING;

    RETURN affected;
END;
$$ LANGUAGE plpgsql;
