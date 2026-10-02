"""Reproduces the measurement behind one rejected design: merging
skill_ids, preferred_skill_ids and skill_groups into a single column.

This file exists because the question keeps coming back, and because the
answer flipped twice under measurement. Reading it should be cheaper than
re-deriving it. It is not a test -- nothing here asserts, and pytest does
not collect it -- it prints numbers you compare against CLAUDE.md's
"Merging the skill columns" section.

What it does NOT do: recommend the merge. At the sizes this project runs
at the win is real but worth nothing; see the trigger condition in
CLAUDE.md. It exists so that when the trigger fires, the work is a day
rather than a fortnight.

Run:  python bench_skill_storage.py            # ~100k synthetic rows
      python bench_skill_storage.py --rows 5   # quick smoke, 5x real data

Everything is built in a scratch schema and dropped at the end. It reads
cleaned_postings but never writes to it.
"""

from __future__ import annotations

import argparse
import statistics
import time

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json

# The two structural markers. Every skill_id is a positive SERIAL, so
# negatives are unreachable values and can never collide with real data --
# which is the whole reason the encoding stays natively queryable.
REQUIRED_END = -1
GROUP_START = -2


# =====================================================================
# Encoding
# =====================================================================
def encode(required: list[int], preferred: list[int],
           groups: list[list[int]]) -> list[int]:
    """[ required... , -1 , preferred... , (-2 , group)... ]"""
    packed = list(required) + [REQUIRED_END] + list(preferred)
    for group in groups:
        packed += [GROUP_START] + list(group)
    return packed


def decode(packed: list[int]) -> tuple[list[int], list[int], list[list[int]]]:
    """Inverse of encode(). Used only to prove the round trip is lossless."""
    required: list[int] = []
    preferred: list[int] = []
    groups: list[list[int]] = []
    target = required
    for value in packed:
        if value == REQUIRED_END:
            target = preferred
        elif value == GROUP_START:
            groups.append([])
            target = groups[-1]
        else:
            target.append(value)
    return required, preferred, groups


# =====================================================================
# Well-formedness -- the guard that makes the encoding safe to WRITE
# =====================================================================
# A CHECK constraint cannot contain a subquery, so the rules live in an
# IMMUTABLE function the CHECK calls -- the same trick skill_group_ids()
# already uses in schema.sql.
WELLFORMED_SQL = """
CREATE FUNCTION bench.wellformed(s int[]) RETURNS bool AS $fn$
SELECT
      COALESCE(array_length(s, 1), 0) >= 1
  AND (SELECT COUNT(*) FROM unnest(s) v WHERE v = -1) = 1
  AND NOT EXISTS (SELECT 1 FROM unnest(s) v WHERE v < -2 OR v = 0)
  -- every group marker sits after the required/preferred divider
  AND NOT EXISTS (
        SELECT 1 FROM unnest(s) WITH ORDINALITY u(v, o)
        WHERE u.v = -2 AND u.o < array_position(s, -1))
  -- no empty group, and no group of one: a group of one is not a choice
  AND NOT EXISTS (
        SELECT 1 FROM (
          SELECT COUNT(*) FILTER (WHERE u.v = -2) OVER (ORDER BY u.o) AS g, u.v
          FROM unnest(s) WITH ORDINALITY u(v, o)) t
        WHERE t.g > 0 GROUP BY t.g HAVING COUNT(*) FILTER (WHERE t.v > 0) < 2)
  -- required and grouped skills stay DISJOINT. Today that is only an
  -- application guarantee in job_database.py; here it is enforced.
  AND NOT EXISTS (
        SELECT 1 FROM (
          SELECT u.v, COUNT(*) FILTER (WHERE u.v = -1) OVER (ORDER BY u.o) AS pr
          FROM unnest(s) WITH ORDINALITY u(v, o)) t
        WHERE t.v > 0 AND t.pr = 0
          AND t.v IN (SELECT t2.v FROM (
                SELECT u.v, COUNT(*) FILTER (WHERE u.v = -2) OVER (ORDER BY u.o) AS g
                FROM unnest(s) WITH ORDINALITY u(v, o)) t2 WHERE t2.g > 0 AND t2.v > 0))
$fn$ LANGUAGE sql IMMUTABLE;
"""

MALFORMED_CASES = [
    ("the -1 divider is missing entirely", [7, 273, -2, 613, 639]),
    ("two -1 dividers", [7, -1, 273, -1, 438]),
    ("a group appears before the -1", [7, -2, 613, 639, -1]),
    ("empty group (two -2 in a row)", [7, -1, -2, -2, 613, 639]),
    ("group of one -- not a choice at all", [7, -1, -2, 613]),
    ("skill both required AND in a group", [7, 613, -1, -2, 613, 639]),
    ("a stray -3 someone invented", [7, -1, -3, 613]),
    ("a zero crept in", [7, 0, -1]),
    ("completely empty array", []),
]


# =====================================================================
# Harness
# =====================================================================
def timed(cur, sql: str, repeats: int = 7) -> tuple[float, list]:
    cur.execute(sql)
    first = cur.fetchall()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        cur.execute(sql)
        cur.fetchall()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), first


def scan_node(cur, sql: str) -> str:
    """The first Scan node in the plan -- enough to see index vs no index."""
    cur.execute("EXPLAIN " + sql)
    for row in cur.fetchall():
        line = row["QUERY PLAN"].strip()
        if "Scan" in line:
            return line.split("(")[0].replace("->", "").strip()
    return "?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=210,
                        help="replicate the real postings this many times "
                             "(210 = ~100k rows, the scale the decision turns on)")
    args = parser.parse_args()

    conn = psycopg2.connect(dbname="jobmarket", user="postgres")
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS bench CASCADE")
    cur.execute("CREATE SCHEMA bench")
    cur.execute("SET search_path = bench, public")
    cur.execute(WELLFORMED_SQL)

    cur.execute("SELECT skill_id FROM public.skills WHERE skill_name = 'AWS'")
    aws = cur.fetchone()["skill_id"]
    cur.execute("SELECT job_id, skill_ids, preferred_skill_ids, skill_groups "
                "FROM public.cleaned_postings ORDER BY job_id")
    real = [(list(r["skill_ids"] or []), list(r["preferred_skill_ids"] or []),
             [list(g) for g in (r["skill_groups"] or [])]) for r in cur.fetchall()]

    # -----------------------------------------------------------------
    print("=" * 78)
    print("1. LOSSLESS -- does the encoding survive a round trip on real data?")
    print("=" * 78)
    mismatches = sum(1 for req, pref, grp in real
                     if decode(encode(req, pref, grp)) != (req, pref, grp))
    print(f"   {len(real)} real postings encoded and decoded: "
          f"{'all exact' if mismatches == 0 else f'{mismatches} MISMATCHED'}")

    # -----------------------------------------------------------------
    print()
    print("=" * 78)
    print("2. SAFE TO WRITE -- what the CHECK constraint refuses")
    print("=" * 78)
    cur.execute("CREATE TABLE bench.guard (skills int[] NOT NULL "
                "CHECK (bench.wellformed(skills)))")
    caught = 0
    for label, arr in MALFORMED_CASES:
        try:
            cur.execute("INSERT INTO bench.guard VALUES (%s)", (arr,))
            print(f"   {label:<42} *** LET THROUGH ***")
        except psycopg2.Error:
            caught += 1
            print(f"   {label:<42} rejected")
    print(f"\n   {caught}/{len(MALFORMED_CASES)} malformed shapes refused")
    bad = [i for i, (req, pref, grp) in enumerate(real)
           if not _wellformed(cur, encode(req, pref, grp))]
    print(f"   all {len(real)} real postings, re-encoded: "
          f"{'all pass' if not bad else f'{len(bad)} FAIL'}")

    # -----------------------------------------------------------------
    print()
    print("=" * 78)
    print(f"3. STORAGE AND SPEED at {len(real) * args.rows:,} rows")
    print("=" * 78)
    _build(cur, real, args.rows)
    _report_sizes(cur)
    _report_queries(cur, aws)

    cur.execute("DROP SCHEMA bench CASCADE")
    print("\n(scratch schema dropped)")


def _wellformed(cur, arr: list[int]) -> bool:
    cur.execute("SELECT bench.wellformed(%s) AS ok", (arr,))
    return cur.fetchone()["ok"]


def _build(cur, real: list, replicas: int) -> None:
    cur.execute("CREATE TABLE bench.split (job_id BIGINT PRIMARY KEY, "
                "skill_ids INT[] NOT NULL, preferred_skill_ids INT[] NOT NULL, "
                "skill_groups JSONB NOT NULL)")
    cur.execute("CREATE TABLE bench.merged (job_id BIGINT PRIMARY KEY, skills INT[] NOT NULL)")

    split_rows, merged_rows, job_id = [], [], 0
    for _ in range(replicas):
        for req, pref, groups in real:
            job_id += 1
            split_rows.append((job_id, req, pref, Json(groups)))
            merged_rows.append((job_id, encode(req, pref, groups)))
    psycopg2.extras.execute_values(cur, "INSERT INTO bench.split VALUES %s",
                                   split_rows, page_size=2000)
    psycopg2.extras.execute_values(cur, "INSERT INTO bench.merged VALUES %s",
                                   merged_rows, page_size=2000)

    # Mirrors schema.sql: one GIN per array, which is what lets the
    # shipped OR-rewrite use an index on each side.
    cur.execute("CREATE INDEX ON bench.split USING GIN (skill_ids)")
    cur.execute("CREATE INDEX ON bench.split USING GIN "
                "(public.skill_group_ids(skill_groups))")
    # One index covers the whole merged array, sentinels included -- the
    # markers become searchable tokens, which is how "offers a choice"
    # turns into an index lookup rather than a scan.
    cur.execute("CREATE INDEX ON bench.merged USING GIN (skills)")
    # array_position is IMMUTABLE, so the required prefix can be indexed too.
    cur.execute("CREATE INDEX ON bench.merged USING GIN "
                "((skills[1:array_position(skills, -1) - 1]))")
    for table in ("bench.split", "bench.merged"):
        cur.execute(f"ANALYZE {table}")


def _report_sizes(cur) -> None:
    print(f"   {'shape':<28}{'heap':>10}{'index':>10}{'TOTAL':>10}{'vs split':>10}")
    print("   " + "-" * 65)
    base = None
    for table, label in [("bench.split", "three columns (current)"),
                         ("bench.merged", "one sentinel INT[]")]:
        cur.execute("SELECT pg_relation_size(%s) h, pg_indexes_size(%s) i, "
                    "pg_total_relation_size(%s) t", (table, table, table))
        size = cur.fetchone()
        base = base or size["t"]
        print(f"   {label:<28}{size['h'] / 1048576:>8.1f} MB{size['i'] / 1048576:>8.1f} MB"
              f"{size['t'] / 1048576:>8.1f} MB{100.0 * (size['t'] - base) / base:>9.0f}%")


def _report_queries(cur, aws: int) -> None:
    queries = {
        # The split side uses the OR-rewrite that api/routers/postings.py
        # actually ships, not the concatenated form it replaced -- comparing
        # against the slower predecessor would flatter the merge.
        "involves AWS  (?skill= filter)": (
            f"SELECT COUNT(*) n FROM bench.split WHERE skill_ids && ARRAY[{aws}] "
            f"OR public.skill_group_ids(skill_groups) && ARRAY[{aws}]",
            f"SELECT COUNT(*) n FROM bench.merged WHERE skills && ARRAY[{aws}]"),
        "requires AWS outright": (
            f"SELECT COUNT(*) n FROM bench.split WHERE skill_ids && ARRAY[{aws}]",
            f"SELECT COUNT(*) n FROM bench.merged "
            f"WHERE skills[1:array_position(skills,-1)-1] && ARRAY[{aws}]"),
        "offers a choice": (
            "SELECT COUNT(*) n FROM bench.split WHERE skill_groups <> '[]'",
            "SELECT COUNT(*) n FROM bench.merged WHERE skills && ARRAY[-2]"),
        # DISTINCT job_id is load-bearing, not decoration. 48 real postings
        # name the same skill in more than one choice group (job 1364 has
        # Azure in four), and skill_group_ids() is array_agg(DISTINCT ...),
        # so the split form collapses them. A bare unnest of the merged
        # array does not, and over-counts demand by 52 mentions. This is
        # the encoding's one sharp edge: the array holds occurrences, the
        # old columns held sets.
        "top-20 demand  (daily snapshot)": (
            "SELECT s, COUNT(*) FROM (SELECT unnest(skill_ids || "
            "public.skill_group_ids(skill_groups)) s FROM bench.split) x "
            "GROUP BY s ORDER BY 2 DESC LIMIT 20",
            "SELECT s, COUNT(*) FROM (SELECT DISTINCT m.job_id, x AS s "
            "FROM bench.merged m, LATERAL unnest(m.skills) x WHERE x > 0) t "
            "GROUP BY s ORDER BY 2 DESC LIMIT 20"),
        "all of Python+AWS  (?skills_all=)": (
            f"SELECT COUNT(*) n FROM bench.split WHERE (skill_ids || "
            f"public.skill_group_ids(skill_groups)) @> ARRAY[362,{aws}]",
            f"SELECT COUNT(*) n FROM bench.merged WHERE skills @> ARRAY[362,{aws}]"),
    }
    print()
    print(f"   {'query':<36}{'split':>10}{'merged':>10}{'speedup':>10}   plan (merged)")
    print("   " + "-" * 96)
    total_split = total_merged = 0.0
    for label, (split_sql, merged_sql) in queries.items():
        split_ms, split_rows = timed(cur, split_sql)
        merged_ms, merged_rows = timed(cur, merged_sql)
        agree = "" if split_rows == merged_rows else "   *** RESULTS DIFFER ***"
        total_split += split_ms
        total_merged += merged_ms
        print(f"   {label:<36}{split_ms:>8.1f}ms{merged_ms:>8.1f}ms"
              f"{split_ms / merged_ms:>9.2f}x   {scan_node(cur, merged_sql)[:28]}{agree}")
    print("   " + "-" * 96)
    print(f"   {'TOTAL':<36}{total_split:>8.1f}ms{total_merged:>8.1f}ms"
          f"{total_split / total_merged:>9.2f}x")


if __name__ == "__main__":
    main()
