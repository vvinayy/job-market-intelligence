"""A closure overturned by a later scrape must be recorded, not erased.

Runs the exact SQL save_records() runs, in its order, inside a transaction
that is always rolled back -- so it exercises the real statements against the
real schema and leaves nothing behind. Skips when Postgres is not reachable.
"""

import inspect

import psycopg2
import pytest
from psycopg2.extras import execute_values

from database import job_database
from database.job_database import REOPENING_SQL, STATE_UPSERT_SQL, connection_params


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**connection_params(), connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="local Postgres not reachable")


@pytest.fixture
def cur():
    conn = psycopg2.connect(**connection_params())
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def _open_posting(cur) -> tuple[int, str, str]:
    cur.execute("""SELECT job_id, source, url FROM posting_state
                   WHERE is_expired IS NOT TRUE AND source = 'naukri' LIMIT 1""")
    return cur.fetchone()


def test_a_reopening_is_recorded_before_the_expiry_is_cleared(cur):
    job_id, source, url = _open_posting(cur)
    cur.execute("""UPDATE posting_state SET is_expired = TRUE, expired_on = DATE '2026-09-01',
                   expiry_basis = 'observed' WHERE job_id = %s""", (job_id,))
    relisted = url + "-relisted"

    rows = [(job_id, source, relisted)]
    execute_values(cur, REOPENING_SQL, rows)
    execute_values(cur, STATE_UPSERT_SQL, rows)

    cur.execute("""SELECT closed_on::text, expiry_basis, old_url, new_url, recorded_from
                   FROM posting_reopenings WHERE job_id = %s AND reopened_on = CURRENT_DATE""",
                (job_id,))
    assert cur.fetchone() == ("2026-09-01", "observed", url, relisted, "scrape")
    cur.execute("SELECT is_expired, expired_on, url FROM posting_state WHERE job_id = %s", (job_id,))
    assert cur.fetchone() == (False, None, relisted)


def test_an_open_posting_seen_again_records_nothing(cur):
    job_id, source, url = _open_posting(cur)
    cur.execute("SELECT count(*) FROM posting_reopenings WHERE job_id = %s", (job_id,))
    before = cur.fetchone()[0]
    execute_values(cur, REOPENING_SQL, [(job_id, source, url)])
    cur.execute("SELECT count(*) FROM posting_reopenings WHERE job_id = %s", (job_id,))
    assert cur.fetchone()[0] == before


def test_save_records_records_before_it_clears():
    # The order is the whole point: after STATE_UPSERT_SQL runs, the closure
    # date and the old link are gone.
    src = inspect.getsource(job_database.save_records)
    assert 0 < src.index("REOPENING_SQL") < src.index("STATE_UPSERT_SQL")
