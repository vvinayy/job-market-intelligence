"""
Database access for the API.

Every query here is parameterised. Values are passed to psycopg2
separately from the SQL string, so the driver escapes them — string
interpolation of user input into SQL is how injection happens, and it
isn't used anywhere in this module.
"""

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool


_pool: SimpleConnectionPool | None = None


def init_pool(minconn: int = 1, maxconn: int = 10):
    """Create the connection pool once at startup.

    Opening a fresh connection per request is slow and wasteful — a pool
    keeps a handful open and hands them out as needed.
    """
    global _pool
    if _pool is not None:
        return

    url = os.environ.get("DATABASE_URL")
    if url:
        _pool = SimpleConnectionPool(minconn, maxconn, dsn=url)
    else:
        _pool = SimpleConnectionPool(
            minconn, maxconn,
            dbname=os.environ.get("PGDATABASE", "jobmarket"),
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD"),
            host=os.environ.get("PGHOST", "localhost"),
            port=os.environ.get("PGPORT", "5432"),
        )


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_cursor():
    """Borrow a connection, yield a dict-returning cursor, return it.

    RealDictCursor makes rows behave like dictionaries, which map
    straight onto Pydantic models without manual column indexing.
    """
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_value(sql: str, params: tuple | dict | None = None):
    """First column of the first row — for COUNT(*) and similar."""
    row = fetch_one(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


class WhereBuilder:
    """Assembles a WHERE clause from optional filters.

    Conditions are appended with %s placeholders and the values kept in
    a parallel list, so nothing user-supplied ever reaches the SQL
    string itself.
    """

    def __init__(self):
        self.clauses: list[str] = []
        self.params: list = []

    def add(self, clause: str, *values):
        """Add a condition. Ignored when every value is None."""
        if all(v is None for v in values):
            return self
        self.clauses.append(clause)
        self.params.extend(values)
        return self

    def add_raw(self, clause: str):
        """A condition with no bound values, e.g. 'salary_min IS NOT NULL'."""
        self.clauses.append(clause)
        return self

    @property
    def sql(self) -> str:
        return " WHERE " + " AND ".join(self.clauses) if self.clauses else ""

    @property
    def values(self) -> tuple:
        return tuple(self.params)
