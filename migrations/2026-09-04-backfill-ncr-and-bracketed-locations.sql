-- ---------------------------------------------------------------------
-- Re-resolve location fragments that today's parser handles.
--
-- Two fixes landed together in cleaning.py:
--
--   * Parentheticals are now stripped BEFORE the comma split. Naukri writes
--     "Hyderabad( Gachibowli, HITEC City )", and splitting first tore it into
--     "Hyderabad( Gachibowli" and "HITEC City )" -- neither a city -- so the
--     posting kept no city at all rather than Hyderabad.
--   * "Delhi NCR" with no separator is aliased. "delhi / ncr", "delhi/ncr"
--     and "ncr" already were; the plain-space spelling was not.
--
-- Four postings, one of which (2926) had no city at all and so appeared in no
-- location filter and in no geographic chart.
--
-- Every value is replayed from a fragment scraped verbatim through the same
-- alias table the parser uses. The mappings are written out rather than
-- derived so this file keeps meaning what it meant the day it ran.
--
--   psql -U postgres -d jobmarket -f migrations/2026-09-04-backfill-ncr-and-bracketed-locations.sql
-- ---------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE _locfix ON COMMIT DROP AS
SELECT c.job_id, m.fragment, ci.city_id
  FROM cleaned_postings c
  JOIN (VALUES
        ('Delhi NCR',             'Delhi / NCR'),
        ('Hyderabad( Gachibowli', 'Hyderabad'),
        ('HITEC City )',          'Hyderabad')
       ) AS m(fragment, city_name) ON m.fragment = ANY(c.unmapped_locations)
  JOIN cities ci ON ci.city_name = m.city_name;

INSERT INTO posting_cities (job_id, city_id)
SELECT DISTINCT job_id, city_id FROM _locfix
ON CONFLICT DO NOTHING;

UPDATE cleaned_postings c
   SET city_ids = (SELECT array_agg(id ORDER BY id)
                     FROM (SELECT DISTINCT unnest(c.city_ids || f.ids) AS id) u),
       unmapped_locations = (
           SELECT COALESCE(array_agg(frag ORDER BY ord), '{}')
             FROM unnest(c.unmapped_locations) WITH ORDINALITY AS t(frag, ord)
            WHERE frag <> ALL(f.frags))
  FROM (SELECT job_id,
               array_agg(DISTINCT city_id)  AS ids,
               array_agg(DISTINCT fragment) AS frags
          FROM _locfix GROUP BY job_id) f
 WHERE c.job_id = f.job_id;

DO $$
DECLARE mismatched int; orphaned int;
BEGIN
    SELECT COUNT(*) INTO mismatched
      FROM cleaned_postings c
      JOIN (SELECT DISTINCT job_id FROM _locfix) t ON t.job_id = c.job_id
     WHERE EXISTS (SELECT 1 FROM posting_cities pc
                    WHERE pc.job_id = c.job_id AND NOT (pc.city_id = ANY(c.city_ids)))
        OR EXISTS (SELECT unnest(c.city_ids) EXCEPT
                   SELECT city_id FROM posting_cities WHERE job_id = c.job_id);
    IF mismatched > 0 THEN
        RAISE EXCEPTION 'city_ids and posting_cities disagree on % posting(s)', mismatched;
    END IF;

    SELECT COUNT(*) INTO orphaned
      FROM cleaned_postings c
      JOIN (SELECT DISTINCT job_id FROM _locfix) t ON t.job_id = c.job_id
     WHERE 'Hyderabad( Gachibowli' = ANY(c.unmapped_locations)
        OR 'HITEC City )'          = ANY(c.unmapped_locations)
        OR 'Delhi NCR'             = ANY(c.unmapped_locations);
    IF orphaned > 0 THEN
        RAISE EXCEPTION '% row(s) still carry a fragment that was resolved', orphaned;
    END IF;
END $$;

COMMIT;

SELECT COUNT(*) AS postings_with_no_city FROM cleaned_postings WHERE city_ids = '{}';
SELECT u AS still_unmapped, COUNT(*) AS postings
  FROM (SELECT unnest(unmapped_locations) u FROM cleaned_postings) x
 GROUP BY u ORDER BY COUNT(*) DESC;
