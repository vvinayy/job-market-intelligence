-- =====================================================================
-- SCHEMA — base tables
--
-- Run this FIRST, before schema_v2.sql / cleaning_setup.sql /
-- roles_setup.sql / trends_setup.sql — all of them assume raw_postings
-- already exists (schema_v2.sql only ALTERs it, never creates it).
--
--   psql -U postgres -d jobmarket -f schema.sql
--
-- raw_postings was originally created ad hoc and never committed to a
-- tracked file. This reconstructs it from the live table, column for
-- column, so a fresh database can actually be built from source.
-- CREATE TABLE IF NOT EXISTS makes it safe to run against a database
-- that already has this table — it's a no-op there, not a reset.
-- =====================================================================

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
