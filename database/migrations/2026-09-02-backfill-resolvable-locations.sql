-- ---------------------------------------------------------------------
-- Re-resolve location fragments that today's parser handles but that were
-- stored before it did.
--
-- resolve_locations() only ever split on commas, so "Gurgaon/Gurugram" --
-- Naukri's way of writing a renamed city -- matched no alias even though both
-- halves are aliased, and landed in unmapped_locations. Two more fragments
-- predate the parenthetical strip ("Hyderabad( Raidurgam )"), one predates the
-- Vijayawada alias, and one is a Bhubaneswar spelling only just added.
--
-- Seven postings. Three of them carry NO cities at all, so they are currently
-- absent from every location filter and from /analytics/locations.
--
-- This invents nothing: every fragment below was scraped verbatim and is
-- replayed through the same alias table the parser uses. The mappings are
-- written out rather than derived so this file keeps meaning what it meant
-- the day it ran, whatever the parser does later.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-02-backfill-resolvable-locations.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE _fix ON COMMIT DROP AS
SELECT c.job_id, m.fragment, ci.city_id
  FROM cleaned_postings c
  JOIN (VALUES
        ('Gurgaon/Gurugram',        'Gurugram'),
        ('Vijayawada',              'Vijayawada'),
        ('Hyderabad( Raidurgam )',  'Hyderabad'),
        ('Hyderabad( HITEC City )', 'Hyderabad'),
        ('Bhubaneshwar',            'Bhubaneswar')
       ) AS m(fragment, city_name) ON m.fragment = ANY(c.unmapped_locations)
  JOIN cities ci ON ci.city_name = m.city_name;

-- The reference table first.
INSERT INTO posting_cities (job_id, city_id)
SELECT job_id, city_id FROM _fix
ON CONFLICT DO NOTHING;

-- Then the same fact on cleaned_postings, which is what production computes
-- off directly. Sorted, to match what resolve_locations() writes.
UPDATE cleaned_postings c
   SET city_ids = (SELECT array_agg(id ORDER BY id)
                     FROM (SELECT DISTINCT unnest(c.city_ids || f.city_id) AS id) u),
       unmapped_locations = array_remove(c.unmapped_locations, f.fragment)
  FROM _fix f
 WHERE c.job_id = f.job_id;

-- Every touched posting must now agree between the two places the fact lives.
DO $$
DECLARE mismatched int;
BEGIN
    SELECT COUNT(*) INTO mismatched
      FROM cleaned_postings c
      JOIN (SELECT DISTINCT job_id FROM _fix) t ON t.job_id = c.job_id
     WHERE EXISTS (SELECT 1 FROM posting_cities pc
                    WHERE pc.job_id = c.job_id AND NOT (pc.city_id = ANY(c.city_ids)))
        OR EXISTS (SELECT unnest(c.city_ids) EXCEPT
                   SELECT city_id FROM posting_cities WHERE job_id = c.job_id);
    IF mismatched > 0 THEN
        RAISE EXCEPTION 'city_ids and posting_cities disagree on % posting(s)', mismatched;
    END IF;
END $$;

COMMIT;

SELECT COUNT(*) AS postings_with_no_city
  FROM cleaned_postings WHERE city_ids = '{}';
SELECT u AS still_unmapped, COUNT(*) AS postings
  FROM (SELECT unnest(unmapped_locations) u FROM cleaned_postings) x
 GROUP BY u ORDER BY COUNT(*) DESC;
