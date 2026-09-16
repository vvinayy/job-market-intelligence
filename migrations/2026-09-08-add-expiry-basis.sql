-- Separate an observed closure from a board's delisting clock.
--
-- is_expired has meant one thing until now: Naukri redirected the URL with
-- expJD=true, which is a closure event. Naukri closures scatter across every
-- age -- measured over 181 expired rows, 0 to 32 days, median 17.
--
-- hirist has no such event. Its hasExpired flips at exactly 150 days after
-- createdTime (measured 2026-09-08, 148d False and 150d True with no
-- exception in 41 samples from 2019 to 2026). That is a delisting rule, not a
-- closure: it says the board stopped serving the listing, never that the
-- employer filled the role.
--
-- Writing both into is_expired without a discriminator would put a
-- deterministic 150-day clock into the same column as a 17-day median event,
-- and /analytics/closures would silently average the two. This column keeps
-- them apart so the mixing has to be deliberate.
--
--   observed  - the board told us this posting is gone (Naukri expJD)
--   delisted  - aged past the board's own cutoff (hirist 150 days)
--
-- Idempotent. Safe to re-run.

-- ---------------------------------------------------------------
ALTER TABLE cleaned_postings
    ADD COLUMN IF NOT EXISTS expiry_basis TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'cleaned_postings_expiry_basis_check') THEN
        ALTER TABLE cleaned_postings
            ADD CONSTRAINT cleaned_postings_expiry_basis_check
            CHECK (expiry_basis IS NULL OR expiry_basis IN ('observed', 'delisted'));
    END IF;
END $$;

-- Every expiry recorded before today came from liveness_checker.py, which
-- only ever ran against Naukri. They are all observations. This must run
-- BEFORE the pairing constraint below, which those rows would otherwise
-- violate the moment it is created.
UPDATE cleaned_postings
   SET expiry_basis = 'observed'
 WHERE is_expired IS TRUE
   AND expiry_basis IS NULL;

-- A basis without an expiry, or an expiry without a basis, is a bug in
-- whatever wrote it. Enforced rather than documented.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'cleaned_postings_expiry_basis_pairing') THEN
        ALTER TABLE cleaned_postings
            ADD CONSTRAINT cleaned_postings_expiry_basis_pairing
            CHECK ((is_expired IS TRUE) = (expiry_basis IS NOT NULL));
    END IF;
END $$;

-- ---------------------------------------------------------------
SELECT source,
       COUNT(*) FILTER (WHERE is_expired IS TRUE)                AS expired,
       COUNT(*) FILTER (WHERE expiry_basis = 'observed')         AS observed,
       COUNT(*) FILTER (WHERE expiry_basis = 'delisted')         AS delisted,
       COUNT(*) FILTER (WHERE is_expired IS NULL)                AS never_checked
  FROM cleaned_postings
 GROUP BY source
 ORDER BY source;
