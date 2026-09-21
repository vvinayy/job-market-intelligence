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
    ('Visakhapatnam',       'Andhra Pradesh'),
    ('Vijayawada',          'Andhra Pradesh')
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
DROP TABLE IF EXISTS posting_qualification_specializations;
DROP TABLE IF EXISTS posting_qualification_degrees;
DROP TABLE IF EXISTS education_degree_specializations;
DROP TABLE IF EXISTS education_specializations;
DROP TABLE IF EXISTS education_degrees;
DROP TABLE IF EXISTS posting_skills;
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS posting_cities;
DROP TABLE IF EXISTS hirist_liveness_observations;
DROP TABLE IF EXISTS posting_sightings;
DROP TABLE IF EXISTS posting_content;
DROP TABLE IF EXISTS posting_state;
DROP TABLE IF EXISTS cleaned_postings;
DROP TABLE IF EXISTS role_categories CASCADE;
DROP TABLE IF EXISTS departments CASCADE;
DROP TABLE IF EXISTS industry_types CASCADE;

-- role_category/department/industry_type are single-valued per posting
-- (unlike skills/cities, which can be several per posting), so each just
-- gets a small dictionary table plus one FK column on cleaned_postings —
-- no junction table needed. job_database.py registers a name here the
-- first time it's seen, same get-or-create pattern as the skills table.
CREATE TABLE role_categories (
    role_category_id  SERIAL PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE
);

CREATE TABLE departments (
    department_id  SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE
);

CREATE TABLE industry_types (
    industry_type_id  SERIAL PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE
);

-- Flattens skill_groups ([[1,2],[3,4]]) to a plain INT[] so it can be
-- unioned with skill_ids. IMMUTABLE so a CHECK constraint may call it —
-- CHECK cannot contain a subquery, which is why this is a function and
-- not inline SQL. Defined before the tables whose constraints use it.
CREATE OR REPLACE FUNCTION skill_group_ids(groups JSONB) RETURNS INT[]
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT COALESCE(array_agg(DISTINCT v::int), '{}')
  FROM jsonb_array_elements(groups) g, jsonb_array_elements_text(g) v
$$;


-- Column ORDER here is deliberate and matches the live table: identity
-- first, then the two things a job description is actually judged on —
-- education, then skills — then everything else. Postgres cannot
-- reorder columns in place, so this order was applied by rebuilding the
-- table (new table, copy, swap, restore constraints/indexes/FKs). If you
-- add a column, `ALTER TABLE ADD COLUMN` appends it to the end and the
-- live table will no longer match this file's order; that's cosmetic and
-- harmless (every query names its columns), but rebuild if it matters.
CREATE TABLE cleaned_postings (
    job_id                 BIGSERIAL PRIMARY KEY,
    fingerprint            TEXT NOT NULL UNIQUE,
    -- Which job board this came from, derived from `url` by
    -- cleaning.source_from_url() and stored rather than re-derived: an ILIKE
    -- on a URL can use no index. A small closed vocabulary we control -- we
    -- write the collectors -- so a CHECK is enough, same call as
    -- working_type. Adding a third board means one entry in SOURCE_HOSTS and
    -- one value here. 'other' is deliberate: an unrecognised host is a real
    -- answer, and folding it into 'naukri' would invent one.
    --
    -- Duplicated onto posting_state as well, deliberately: the liveness queue
    -- filters on it, and without a copy there that query would join back here
    -- purely to read it -- measured 38 pages against 274. It never changes
    -- after insert, and one upsert writes both.
    source                 TEXT NOT NULL DEFAULT 'other'
                               CHECK (source IN ('naukri', 'hirist', 'other')),
    title                  TEXT,
    company                TEXT,

    -- Mined from description text via skill_taxonomy.extract_certifications()
    -- — a credential someone holds, a different kind of signal from a
    -- tool/language skill, kept separate from posting_skills rather
    -- than folded in.
    certifications            TEXT[],

    experience_min         INT,
    experience_max         INT,
    salary_min              NUMERIC,
    salary_max              NUMERIC,

    unmapped_locations      TEXT[],

    -- Cities the description names when it names none of this posting's own
    -- -- the signature of a description belonging to a different job, which
    -- nothing else here can see: company, title, location and experience all
    -- stay correct when only the body is wrong. NULL/empty means no conflict.
    --
    -- A flag, never a rejection. Roughly half of what it catches is a
    -- recruiter naming a different office in the body, so the posting is
    -- always written in full and marked, and a human decides. Recomputed on
    -- every sighting, so it clears itself the moment Naukri serves the right
    -- description.
    --
    -- Stays HERE rather than moving to posting_content with the description
    -- it is derived from: it is 13 bytes, and ?description_flagged= filters
    -- list queries on it. Moving it would put the widest table in the schema
    -- on the list path to read one small array.
    description_foreign_cities TEXT[],

    -- Closed, stable vocabularies -- cleaning.py already collapses every
    -- spelling Naukri uses down to one canonical value per category
    -- (normalize_working_type / EMPLOYMENT_TYPES / CONTRACT_TYPES), so a
    -- CHECK constraint is enough to guard against a bug in that code; a
    -- reference table would only add a JOIN for a vocabulary this small
    -- and this unlikely to grow.
    working_type            TEXT CHECK (working_type IN ('On-site', 'Hybrid', 'Remote')),
    -- Two values and no prospect of a third, so a boolean rather than
    -- TEXT + CHECK. contract_type stays TEXT: it already carries five.
    is_full_time            BOOLEAN,
    contract_type           TEXT CHECK (contract_type IN
                                 ('Permanent', 'Contract', 'Temporary', 'Internship', 'Freelance')),

    role_family             TEXT,
    -- Inferred from the title alone, not experience_min/max — a
    -- different, often-absent signal. NULL means the title carried no
    -- seniority marker at all (the common case), not "mid-level".
    seniority_level          TEXT,
    role_category_id        INT REFERENCES role_categories(role_category_id),
    -- Naukri's own classification (e.g. "Back End Developer") — distinct
    -- from role_category (e.g. "Software Development") and from
    -- role_family (our own regex-derived classification of the title).
    -- Recruiter-picked from a dropdown, so it is noisy: the same title
    -- gets different values on different postings, and it occasionally
    -- contradicts the JD outright. A raw signal, not ground truth.
    naukri_role              TEXT,
    department_id            INT REFERENCES departments(department_id),
    industry_type_id         INT REFERENCES industry_types(industry_type_id),

    posted_date               DATE,
    posted_raw                TEXT,
    openings                  INT,

    -- Which jobmarket.bat search URL surfaced this posting —
    -- lets a query answer "which searches are actually productive"
    -- instead of only ever seeing the merged result.
    source_search             TEXT,
    first_seen_date          DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Only three indexes here, down from nine. Five were GIN indexes over array
-- columns that were byte-identical copies of the child tables and had never
-- been scanned once; they went with their columns in the 2026-09-15 split.
-- The liveness index moved to posting_state, which now holds what it covers.
CREATE INDEX IF NOT EXISTS idx_cleaned_postings_source
    ON cleaned_postings (source);


-- ---------------------------------------------------------------------
-- THE THREE SATELLITES — one row per posting each, split off by how
-- often they are WRITTEN rather than by what they mean.
--
-- Measured on 920 live rows before the split: the spine carried 1,194
-- bytes over 45 columns at 4.4 rows per 8 kB page, so a scrape that only
-- bumped last_seen_date rewrote a ~2,230-byte tuple. Afterwards the spine
-- is 352 bytes and a re-sighting writes ~77. See ARCHITECTURE.md §3.
-- ---------------------------------------------------------------------

-- Liveness, written by liveness_checker.py. A search never surfaces an
-- expired posting, so nothing else in the pipeline can ever reach these rows
-- -- the checker visits stored URLs directly, which is why url lives here
-- beside the columns it writes.
CREATE TABLE posting_state (
    job_id          BIGINT PRIMARY KEY
                    REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    source          TEXT NOT NULL CHECK (source IN ('naukri', 'hirist', 'other')),
    url             TEXT NOT NULL,
    -- NULL = never checked, which must stay distinct from FALSE = checked
    -- and alive. Same three-state shape as is_full_time; never test for
    -- truthiness.
    is_expired      BOOLEAN,
    -- The date we CONFIRMED expiry, not the date it expired -- Naukri never
    -- says the latter. The gap to last_checked_on is the measurement error.
    expired_on      DATE,
    last_checked_on DATE,
    -- 'observed' = a real closure the checker saw. 'delisted' = aged past
    -- hirist's 150-day cutoff, which is a calendar tick and NOT a closure.
    -- /analytics/closures reads 'observed' only.
    expiry_basis    TEXT CHECK (expiry_basis IS NULL
                                OR expiry_basis IN ('observed', 'delisted')),
    -- An expiry date on a posting that isn't expired is a bug, not a state.
    CONSTRAINT posting_state_expired_on_requires_expired
        CHECK (expired_on IS NULL OR is_expired IS TRUE),
    -- Paired so neither half can be written without the other.
    CONSTRAINT posting_state_basis_pairs_with_expired
        CHECK ((is_expired IS TRUE) = (expiry_basis IS NOT NULL))
);

-- The checker's queue is "not already dead, not already done today", and the
-- dead half of the table grows without ever being queried -- hence partial.
-- source is in the key because the queue is Naukri-only.
CREATE INDEX IF NOT EXISTS idx_posting_state_queue
    ON posting_state (source, last_checked_on)
    WHERE is_expired IS NOT TRUE;


-- 466 of the old row's 1,194 bytes, read on the detail path alone.
--
-- NOTE: description_hash used to sit beside this. Deduplication is by
-- `fingerprint` (company + title + location + experience), so the hash never
-- distinguished a posting from another -- it only sped up the ad-hoc "which
-- postings share verbatim JD text" query, whose index was never once scanned.
-- That query still works without it:
--   SELECT md5(description) FROM posting_content
--     JOIN cleaned_postings USING (job_id) GROUP BY 1 HAVING COUNT(DISTINCT company) > 1
-- Postgres cannot btree-index `description` directly (rows exceed the
-- 2704-byte limit), so if it ever needs an index again, index md5(description)
-- as an expression rather than storing a column.
--
-- NOTE: responsibilities_text / requirements_text used to sit here too. They
-- are a pure function of `description` (cleaning.py::split_description_sections),
-- verified byte-identical on every row, so storing them cost ~24% of the table
-- to duplicate text already present. The API computes them at read time.
-- Don't re-add them as columns.
CREATE TABLE posting_content (
    job_id      BIGINT PRIMARY KEY
                REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    description TEXT
);


-- Everything a re-sighting changes, and nothing else. This is the only table
-- a daily scrape writes when the advert itself has not changed.
CREATE TABLE posting_sightings (
    job_id                    BIGINT PRIMARY KEY
                              REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    last_seen_date            DATE NOT NULL DEFAULT CURRENT_DATE,
    times_seen                INT NOT NULL DEFAULT 1,
    -- Naukri shows this three ways: a plain exact number ("44"), a floor once
    -- past some threshold ("100+"), or a ceiling for very new postings ("Less
    -- than 10"). applicant_count is always the digit found either way;
    -- applicant_count_qualifier records which direction the ambiguity runs --
    -- NULL means the number was exact, not that the qualifier is unknown.
    -- 'at_least'/'less_than' point opposite directions, so collapsing both
    -- into a bare int would be actively wrong for the 'less_than' case.
    applicant_count           INT,
    applicant_count_qualifier TEXT,
    -- Shown inline in the posting header, sourced from AmbitionBox.
    -- company_reviews is Naukri's own rounded figure ("50.5K Reviews")
    -- expanded from its K/M shorthand, not a more precise count than the
    -- source has. These sit here rather than on a companies table because
    -- they are observations at scrape time: measured 2026-09-15, 458
    -- companies produced 556 distinct (company, rating, reviews) triples, so
    -- the same company carries different ratings across postings.
    company_rating            NUMERIC,
    company_reviews           INT,
    -- Recognition badges from the inline "About the company" block
    -- (e.g. "Fortune India 500 (2023)", "Highly Rated by Women").
    -- Confirmed from raw HTML that short entries like "TOP" are
    -- genuinely what Naukri shows, not a truncation artifact.
    company_badges            TEXT[]
);

-- snapshot_daily_skills() selects the postings seen today; a sequential scan
-- of the spine was 416 pages.
CREATE INDEX IF NOT EXISTS idx_posting_sightings_last_seen
    ON posting_sightings (last_seen_date);


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
    -- Mirrored onto cleaned_postings too; see the note there for why
    -- this is JSONB and not nested inside skill_ids.
    skill_groups          JSONB  NOT NULL DEFAULT '[]',
    CONSTRAINT preferred_skills_subset_of_skills
        CHECK (preferred_skill_ids <@ (skill_ids || skill_group_ids(skill_groups)))
);

-- Two indexes because the two query shapes need different opclasses: the
-- expression index answers "does this posting offer skill 96 as an option"
-- (= ANY / &&), the jsonb_path_ops one answers "which postings offer exactly
-- this SET" (@>). The expression index is only possible because
-- skill_group_ids() is IMMUTABLE. ?skill= is deliberately written as two
-- separate && tests OR'd together so both can be used -- concatenating first
-- builds a new array per row and no index can cover it, measured 14x slower.
CREATE INDEX IF NOT EXISTS idx_posting_skills_skill_ids ON posting_skills USING GIN (skill_ids);
-- Containment cannot be split the way overlap can: "all of these, in a OR b"
-- is not "all in a, or all in b". So ?skills_all= must use the concatenation,
-- and only an index over exactly that expression can serve it -- proven
-- 2026-09-15, it sequential-scans even with enable_seqscan off without this.
CREATE INDEX IF NOT EXISTS idx_posting_skills_all
    ON posting_skills USING GIN ((skill_ids || skill_group_ids(skill_groups)));
CREATE INDEX IF NOT EXISTS idx_posting_skills_skill_group_ids
    ON posting_skills USING GIN (skill_group_ids(skill_groups));


-- ---------------------------------------------------------------------
-- POSTING_SKILL_DEMAND — "every skill this posting would accept", once.
--
-- skill_ids holds outright requirements; a skill offered as one of several
-- alternatives ("AWS or Azure") lives in skill_groups and is normally not
-- repeated there. Demand is both together, and reading skill_ids alone is
-- an undercount -- eight read paths did exactly that until 2026-09-17 and
-- reported AWS in 295 postings against a true 419.
--
-- The rule was written out by hand at twelve sites. This view is the one
-- copy for every caller that wants the skills as ROWS. Exactly three
-- callers still write the concatenation, because an array is what they
-- operate on: ?skills_all= (containment), the preferred-subset CHECK
-- above, and idx_posting_skills_all. ?skill= is NOT one of them -- it
-- tests the two arrays separately and ORs the results, so that both GIN
-- indexes can serve it; concatenating first builds a new array per row
-- and no index can cover it, measured 14x slower.
--
-- DISTINCT is load-bearing, not tidiness. The two columns are meant to be
-- disjoint but are not guaranteed to be: the 2026-09-16 merge migration
-- repointed "Iac Terraform" -> Terraform inside skill_ids on postings that
-- already offered Terraform in a group, and its verification checked for
-- duplicates WITHIN skill_ids but never ACROSS the two columns. Four rows
-- came out holding one skill twice (jobs 2250 Django, 2695 Snowflake, 2952
-- and 2963 Terraform), which inflated Terraform to 150 postings against a
-- true 148 on every COUNT(*) read path. The rows are not wrong -- such a
-- posting really does demand the skill outright AND list it as an
-- alternative, and deleting either copy would destroy a real fact -- so
-- this is deduplicated on read rather than repaired in place.
--
-- Postgres inlines a view this simple, so a caller's plan is unchanged.
-- snapshot_daily_skills() was already immune via COUNT(DISTINCT job_id),
-- which is why skill_daily_counts history never recorded the inflation and
-- this fix introduces no step in the trend series.
CREATE OR REPLACE VIEW posting_skill_demand AS
    SELECT DISTINCT ps.job_id, u.skill_id
      FROM posting_skills ps,
           unnest(ps.skill_ids || skill_group_ids(ps.skill_groups)) AS u(skill_id);


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
-- EDUCATION_DEGREES / EDUCATION_SPECIALIZATIONS — posting_qualifications
-- above keeps Naukri's flattened display string per level; these break
-- it down further into individually referenceable facts, the same way
-- skills got their own dictionary table instead of staying free text.
--
-- Naukri renders each level as ONE flattened <span> (confirmed from raw
-- HTML), e.g. "MCA in Any Specialization, MS/M.Sc(Science) in Any
-- Specialization" — the comma separates either a new degree or another
-- specialization for the degree just before it, with no punctuation
-- telling the two cases apart. cleaning.py's _parse_field_of_study()
-- resolves that ambiguity against a small fixed vocabulary of known
-- degree names (verified against every distinct value seen in this
-- project's data). A slash-joined entry ("B.Tech / B.E.",
-- "MS/M.Sc(Science)") is Naukri listing two alternative credentials,
-- split into two separate degree rows rather than kept as one combined
-- string.
--
-- education_degree_specializations: every distinct (degree, specialization)
-- pairing actually seen, each with its own id — the same "assign an id,
-- reference it from an array" treatment as skills, applied one level
-- deeper. Without this, posting_qualification_specializations would need
-- either one row per posting-degree-specialization triple (exploded, the
-- same repeated-row problem skills solved by becoming a dictionary) or a
-- flat per-posting array that loses which specialization belongs to
-- which degree. Going through this dictionary keeps it one row per
-- posting AND keeps the pairing fully recoverable by joining through it.
--
-- posting_qualification_degrees: one row per posting, accepted_degree_ids
-- array — exactly mirrors posting_skills' shape (no per-degree extra
-- attribute needed here, unlike specializations below).
-- posting_qualification_specializations: one row per posting,
-- accepted_degree_specialization_ids array referencing the dictionary
-- above. Omitted entirely for a posting whose accepted degrees carry no
-- specialization info at all (e.g. "Any Graduate"). Both arrays are
-- named accepted_* rather than a bare noun, since multiple ids here
-- mean "any one satisfies the posting" (OR) -- unlike posting_skills'
-- skill_ids, where multiple ids are genuinely all wanted together, not
-- alternatives. Same INT[] type either way, so the name has to carry
-- the distinction.
-- ---------------------------------------------------------------------
CREATE TABLE education_degrees (
    degree_id    SERIAL PRIMARY KEY,
    degree_name  TEXT NOT NULL UNIQUE,
    level        TEXT NOT NULL
);

CREATE TABLE education_specializations (
    specialization_id    SERIAL PRIMARY KEY,
    specialization_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE education_degree_specializations (
    degree_specialization_id  SERIAL PRIMARY KEY,
    degree_id                 INT NOT NULL REFERENCES education_degrees(degree_id),
    specialization_id         INT NOT NULL REFERENCES education_specializations(specialization_id),
    UNIQUE (degree_id, specialization_id)
);

CREATE TABLE posting_qualification_degrees (
    job_id                BIGINT NOT NULL PRIMARY KEY REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    accepted_degree_ids  INT[]  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_posting_qualification_degrees_accepted_ids
    ON posting_qualification_degrees USING GIN (accepted_degree_ids);

CREATE TABLE posting_qualification_specializations (
    job_id                               BIGINT NOT NULL PRIMARY KEY REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    accepted_degree_specialization_ids  INT[]  NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_posting_qualification_specializations_accepted_ids
    ON posting_qualification_specializations USING GIN (accepted_degree_specialization_ids);


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


-- ---------------------------------------------------------------------
-- liveness_runs -- one row per liveness_checker.py run.
--
-- Exists for one number: MIN(started_at) is the date closure detection
-- began, and every exposure-adjusted rate needs it. Without it the only
-- available proxy is first_seen_date, which counts days before anything
-- was checking as if a closure could have been detected then -- measured
-- at 8,280 posting-days against 1,806 actually observed, a 4.6x inflated
-- denominator that reordered the entire role ranking.
--
-- Deliberately not IF NOT EXISTS-free like the postings tables: this
-- must survive a schema.sql re-run, because losing the start date
-- silently restores the wrong denominator rather than erroring.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS liveness_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    checked         INT,
    expired         INT,
    live            INT,
    unknown         INT,
    -- NULL on a clean run. Set when a safety valve fired or the write
    -- failed, so an aborted run is still dated -- observation did not
    -- stop just because that night's results were discarded.
    aborted_reason  TEXT
);

CREATE INDEX IF NOT EXISTS idx_liveness_runs_started_at ON liveness_runs (started_at);


-- ---------------------------------------------------------------------
-- HIRIST_LIVENESS_OBSERVATIONS - what a hirist check WOULD have concluded.
--
-- The rule (liveness.classify_hirist) turned out to be a 150-day clock rather
-- than a closure signal, so it is NOT wired into liveness_checker.py and must
-- not be. This table stays because the probe writes here and nowhere else: a
-- wrong rule costs a wrong row in an observation log rather than a fabricated
-- closure that then counts as exposure in /analytics/closures.
--
-- One row per posting per day. Added to schema.sql on 2026-09-15; before that
-- it existed only in migrations/2026-09-03-hirist-liveness-observations.sql,
-- so a fresh install silently lacked it.
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
