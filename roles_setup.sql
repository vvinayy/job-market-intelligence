-- =====================================================================
-- ROLE FAMILIES
--
-- Job titles are free text: "Sr Machine Learning Engineer", "ML Engineer"
-- and "Machine Learning Engineer II" are three strings for one role. This
-- maps them onto a fixed set of families so they can be counted together.
--
-- Run once:
--   psql -U postgres -d jobmarket -f roles_setup.sql
-- Then re-run clean_and_populate() to fill the new column.
-- =====================================================================


-- ---------------------------------------------------------------------
-- PATTERNS — matched against the lowercased title.
--
-- `priority` decides which wins when several match: "Senior Data
-- Engineer" matches both 'data engineer' and 'engineer', so the more
-- specific pattern needs the lower number. Without ordering, every
-- specific role would collapse into a generic one.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_patterns (
    pattern      TEXT PRIMARY KEY,
    role_family  TEXT NOT NULL,
    priority     INT  NOT NULL
);

INSERT INTO role_patterns (pattern, role_family, priority) VALUES
    -- Data and AI (most specific first)
    ('machine learning',      'ML Engineer',        10),
    ('ml engineer',           'ML Engineer',        10),
    ('mlops',                 'ML Engineer',        10),
    ('deep learning',         'ML Engineer',        10),
    ('data scientist',        'Data Scientist',     10),
    ('data science',          'Data Scientist',     11),
    ('ai engineer',           'AI Engineer',        10),
    ('artificial intelligence','AI Engineer',       11),
    ('generative ai',         'AI Engineer',        10),
    ('gen ai',                'AI Engineer',        10),
    ('nlp',                   'AI Engineer',        11),
    ('data engineer',         'Data Engineer',      10),
    ('etl developer',         'Data Engineer',      10),
    ('big data',              'Data Engineer',      12),
    ('data analyst',          'Data Analyst',       10),
    ('business intelligence', 'Data Analyst',       11),
    ('bi developer',          'Data Analyst',       10),
    ('data architect',        'Architect',          10),

    -- Infrastructure
    ('devops',                'DevOps Engineer',    10),
    ('site reliability',      'DevOps Engineer',    10),
    ('sre',                   'DevOps Engineer',    11),
    ('platform engineer',     'DevOps Engineer',    11),
    ('cloud engineer',        'Cloud Engineer',     10),
    ('cloud architect',       'Architect',          10),
    ('infrastructure',        'Infrastructure',     12),
    ('network engineer',      'Infrastructure',     11),
    ('system administrator',  'Infrastructure',     11),

    -- Software engineering
    ('full stack',            'Full Stack Developer', 10),
    ('fullstack',             'Full Stack Developer', 10),
    ('frontend',              'Frontend Developer',  10),
    ('front end',             'Frontend Developer',  10),
    ('ui developer',          'Frontend Developer',  10),
    ('react developer',       'Frontend Developer',  10),
    ('angular developer',     'Frontend Developer',  10),
    ('backend',               'Backend Developer',   10),
    ('back end',              'Backend Developer',   10),
    ('java developer',        'Backend Developer',   11),
    ('python developer',      'Backend Developer',   11),
    ('.net developer',        'Backend Developer',   11),
    ('node developer',        'Backend Developer',   11),
    ('android developer',     'Mobile Developer',    10),
    ('ios developer',         'Mobile Developer',    10),
    ('mobile developer',      'Mobile Developer',    10),
    ('flutter',               'Mobile Developer',    11),

    -- Quality and security
    ('qa engineer',           'QA / Test',          10),
    ('test engineer',         'QA / Test',          10),
    ('automation test',       'QA / Test',          10),
    ('quality assurance',     'QA / Test',          11),
    ('sdet',                  'QA / Test',          10),
    ('security engineer',     'Security',           10),
    ('cyber security',        'Security',           10),
    ('information security',  'Security',           11),

    -- Analysis and leadership
    ('business analyst',      'Business Analyst',   10),
    ('product manager',       'Product',            10),
    ('product owner',         'Product',            10),
    ('scrum master',          'Product',            11),
    ('solution architect',    'Architect',          10),
    ('technical architect',   'Architect',          10),
    ('architect',             'Architect',          14),
    ('engineering manager',   'Engineering Manager', 10),
    ('tech lead',             'Engineering Manager', 11),

    -- Generic catch-alls, lowest priority so anything specific wins
    ('software engineer',     'Software Engineer',  20),
    ('software developer',    'Software Engineer',  20),
    ('developer',             'Software Engineer',  25),
    ('engineer',              'Software Engineer',  30),
    ('analyst',               'Business Analyst',   30),
    ('consultant',            'Consultant',         28)
ON CONFLICT (pattern) DO NOTHING;


-- ---------------------------------------------------------------------
-- classify_role() — first matching pattern by priority wins.
-- Unmatched titles return 'Other' rather than NULL, so they still
-- appear in counts and the size of the gap is visible.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION classify_role(raw_title TEXT) RETURNS TEXT AS $$
    SELECT COALESCE(
        (SELECT role_family
         FROM role_patterns
         WHERE lower(COALESCE(raw_title, '')) LIKE '%' || pattern || '%'
         ORDER BY priority
         LIMIT 1),
        'Other'
    );
$$ LANGUAGE sql IMMUTABLE;


-- ---------------------------------------------------------------------
-- Add the column and backfill.
-- ---------------------------------------------------------------------
ALTER TABLE cleaned_postings ADD COLUMN IF NOT EXISTS role_family TEXT;

UPDATE cleaned_postings SET role_family = classify_role(title);
