-- ---------------------------------------------------------------------
-- RESTORE the 8 legacy SQL cleaning functions dropped by
-- 2026-09-28-drop-legacy-sql-cleaning-functions.sql. Exact definitions,
-- captured from the live jobmarket database with pg_get_functiondef()
-- immediately before the drop. Only needed to undo that migration:
--   psql -U postgres -d jobmarket -f migrations/2026-09-28-restore-legacy-sql-cleaning-functions.sql
-- Note: clean_and_populate() reads raw_postings, which no longer exists, so
-- restoring it does not make it callable. Likewise classify_role() reads a
-- role_patterns table that was already gone when this was captured.
-- ---------------------------------------------------------------------

BEGIN;

-- Recreate them exactly as they were, dangling references included -- the
-- same setting pg_dump uses. Without it classify_role() refuses to compile.
SET LOCAL check_function_bodies = off;

CREATE OR REPLACE FUNCTION public.classify_role(raw_title text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT COALESCE(
        (SELECT role_family
         FROM role_patterns
         WHERE lower(COALESCE(raw_title, '')) LIKE '%' || pattern || '%'
         ORDER BY priority
         LIMIT 1),
        'Other'
    );
$function$;

CREATE OR REPLACE FUNCTION public.clean_and_populate()
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.normalize_skill(raw_skill text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT COALESCE(
        (SELECT canonical_name FROM skill_aliases WHERE alias = lower(trim(raw_skill))),
        initcap(trim(raw_skill))
    );
$function$;

CREATE OR REPLACE FUNCTION public.normalize_working_type(raw text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT CASE
        WHEN lower(raw) LIKE '%hybrid%' THEN 'Hybrid'
        WHEN lower(raw) LIKE '%remote%' THEN 'Remote'
        WHEN lower(raw) LIKE '%work from home%' THEN 'Remote'
        WHEN lower(raw) LIKE '%office%' THEN 'On-site'
        ELSE 'On-site'
    END;
$function$;

CREATE OR REPLACE FUNCTION public.parse_contract_type(raw text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT part FROM (
        SELECT initcap(trim(p)) AS part
        FROM unnest(string_to_array(COALESCE(raw, ''), ',')) AS p
    ) x
    WHERE lower(part) IN ('permanent', 'contract', 'contractual', 'temporary', 'internship', 'freelance')
    LIMIT 1;
$function$;

CREATE OR REPLACE FUNCTION public.parse_employment_type(raw text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT part FROM (
        SELECT initcap(trim(p)) AS part
        FROM unnest(string_to_array(COALESCE(raw, ''), ',')) AS p
    ) x
    WHERE lower(part) IN ('full time', 'full-time', 'part time', 'part-time')
    LIMIT 1;
$function$;

CREATE OR REPLACE FUNCTION public.parse_range_max(raw text)
 RETURNS numeric
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT CASE
        WHEN raw IS NULL THEN NULL
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'))[2]::NUMERIC
        ELSE NULL   -- "5+ years" has no upper bound; don't invent one
    END;
$function$;

CREATE OR REPLACE FUNCTION public.parse_range_min(raw text)
 RETURNS numeric
 LANGUAGE sql
 IMMUTABLE
AS $function$
    SELECT CASE
        WHEN raw IS NULL THEN NULL
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)'))[1]::NUMERIC
        WHEN raw ~ '(\d+(?:\.\d+)?)\s*\+'
            THEN (regexp_match(raw, '(\d+(?:\.\d+)?)\s*\+'))[1]::NUMERIC
        ELSE NULL
    END;
$function$;

COMMIT;
