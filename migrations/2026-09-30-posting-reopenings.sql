-- ---------------------------------------------------------------------
-- posting_reopenings: a closure overturned by a later scrape.
--
-- Until now a search re-finding a closed posting's fingerprint cleared the
-- expiry in posting_state and left no record that it had ever closed -- 20 of
-- 382 logged closures by 2026-09-30. save_records() now writes a row here just
-- before clearing. Same definition as schema.sql. Idempotent.
--
-- Past reopenings are rebuilt from the logs by
-- 2026-09-30-backfill-posting-reopenings.py, run after this file.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS posting_reopenings (
    job_id        BIGINT NOT NULL REFERENCES cleaned_postings(job_id) ON DELETE CASCADE,
    reopened_on   DATE   NOT NULL DEFAULT CURRENT_DATE,
    closed_on     DATE,
    expiry_basis  TEXT CHECK (expiry_basis IS NULL
                              OR expiry_basis IN ('observed', 'delisted')),
    old_url       TEXT   NOT NULL,
    new_url       TEXT   NOT NULL,
    recorded_from TEXT   NOT NULL DEFAULT 'scrape'
                  CHECK (recorded_from IN ('scrape', 'log_backfill')),
    PRIMARY KEY (job_id, reopened_on)
);
