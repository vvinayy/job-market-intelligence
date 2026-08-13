"""
Trends — how demand changes over time.

These read from skill_daily_counts, which is populated once per day by
snapshot_daily_skills(). Every endpoint here returns little or nothing
until several days of history exist; /coverage exists so a client can
check before rendering an empty chart.
"""

from datetime import date

from fastapi import APIRouter, Query

from ..database import fetch_all, fetch_one, fetch_value
from ..models import Coverage, TrendPoint, Mover, FirstAppearance

router = APIRouter(prefix="/trends", tags=["trends"])


@router.get("/coverage", response_model=Coverage,
            summary="How much history exists")
def coverage():
    row = fetch_one("""
        SELECT COUNT(DISTINCT snapshot_date)::int AS days_recorded,
               MIN(snapshot_date) AS earliest,
               MAX(snapshot_date) AS latest,
               COUNT(DISTINCT skill)::int AS distinct_skills
        FROM skill_daily_counts
    """) or {}

    days = row.get("days_recorded") or 0
    # Surfaced explicitly so a client can decide what to show rather
    # than requesting a delta and getting an empty list back.
    row["daily_delta_available"] = days >= 2
    row["baseline_delta_available"] = days >= 8
    return Coverage(**row)


@router.get("/skills", response_model=list[TrendPoint],
            summary="Demand over time for given skills")
def skill_series(
    skill: list[str] = Query(..., description="One or more skills to plot"),
    since: date | None = Query(None),
    until: date | None = Query(None),
):
    params: list = [list(skill)]
    clauses = ["skill = ANY(%s)"]
    if since:
        clauses.append("snapshot_date >= %s")
        params.append(since)
    if until:
        clauses.append("snapshot_date <= %s")
        params.append(until)

    return fetch_all(f"""
        SELECT snapshot_date, skill, posting_count
        FROM skill_daily_counts
        WHERE {' AND '.join(clauses)}
        ORDER BY snapshot_date, skill
    """, tuple(params))


@router.get("/movers", response_model=list[Mover],
            summary="Biggest changes in demand")
def movers(
    comparison: str = Query("auto", pattern="^(auto|previous_day|rolling_7d)$",
        description="'auto' picks rolling_7d when enough history exists"),
    limit: int = Query(25, ge=1, le=200),
    direction: str = Query("both", pattern="^(both|up|down)$"),
    on: date | None = Query(None, description="Defaults to the latest snapshot"),
):
    days = fetch_value(
        "SELECT COUNT(DISTINCT snapshot_date) FROM skill_daily_counts") or 0

    mode = comparison
    if mode == "auto":
        mode = "rolling_7d" if days >= 8 else "previous_day"
    if mode == "rolling_7d" and days < 8:
        return []
    if mode == "previous_day" and days < 2:
        return []

    target = on or fetch_value("SELECT MAX(snapshot_date) FROM skill_daily_counts")

    if mode == "previous_day":
        sql = """
            SELECT skill, snapshot_date, posting_count,
                   previous_count::float, change::float, pct_change::float,
                   'previous_day' AS comparison
            FROM skill_delta_daily
            WHERE snapshot_date = %s AND change IS NOT NULL
        """
        order_col = "change"
    else:
        sql = """
            SELECT skill, snapshot_date, posting_count,
                   baseline_7d::float        AS previous_count,
                   change_vs_baseline::float AS change,
                   pct_vs_baseline::float    AS pct_change,
                   'rolling_7d' AS comparison
            FROM skill_delta_vs_baseline
            WHERE snapshot_date = %s
        """
        order_col = "change_vs_baseline"

    if direction == "up":
        sql += f" AND {order_col} > 0"
        ordering = f"ORDER BY {order_col} DESC"
    elif direction == "down":
        sql += f" AND {order_col} < 0"
        ordering = f"ORDER BY {order_col} ASC"
    else:
        ordering = f"ORDER BY ABS({order_col}) DESC"

    return fetch_all(f"{sql} {ordering} LIMIT %s", (target, limit))


@router.get("/new-skills", response_model=list[FirstAppearance],
            summary="Skills recorded for the first time")
def new_skills(
    since: date | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    clause, params = "", []
    if since:
        clause = "WHERE first_seen >= %s"
        params.append(since)
    params.append(limit)

    return fetch_all(f"""
        SELECT skill, first_seen, days_present
        FROM skill_first_appearances
        {clause}
        ORDER BY first_seen DESC, skill
        LIMIT %s
    """, tuple(params))
