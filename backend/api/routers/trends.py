"""
Trends — how demand changes over time.

These read from skill_daily_counts, which is populated once per day by
snapshot_daily_skills(). Every endpoint here returns little or nothing
until several days of history exist; /coverage exists so a client can
check before rendering an empty chart.
"""

from datetime import date, timedelta

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
               COUNT(DISTINCT skill)::int AS distinct_skills,
               COALESCE((MAX(snapshot_date) - MIN(snapshot_date) + 1), 0)::int
                   AS calendar_days_spanned
        FROM skill_daily_counts
    """) or {}

    days = row.get("days_recorded") or 0
    # Stated outright rather than left to be inferred from two numbers. A
    # snapshot only happens when the machine is on, and a day not recorded is
    # gone for good -- so this is a permanent hole in the history, not a lag.
    row["days_missing"] = max(0, (row.get("calendar_days_spanned") or 0) - days)
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
    source: str | None = Query(
        None, description="Limit to one job board, e.g. 'naukri'. Omit to sum "
                          "every source, which is what this returned when "
                          "there was only one."),
):
    params: list = [list(skill)]
    clauses = ["skill = ANY(%s)"]
    if since:
        clauses.append("snapshot_date >= %s")
        params.append(since)
    if until:
        clauses.append("snapshot_date <= %s")
        params.append(until)
    if source:
        clauses.append("source = %s")
        params.append(source)

    # SUM rather than a bare select: the snapshot holds one row per
    # (date, skill, source), so a skill carried by two boards would otherwise
    # come back as two points on the same date and silently double the line.
    return fetch_all(f"""
        SELECT snapshot_date, skill, SUM(posting_count)::int AS posting_count
        FROM skill_daily_counts
        WHERE {' AND '.join(clauses)}
        GROUP BY snapshot_date, skill
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
    skill: list[str] | None = Query(None, description="Restrict to specific skills; omit for the biggest movers overall"),
    max_gap: int = Query(1, ge=1, le=60,
        description="previous_day mode: reject comparisons reaching back more "
                    "than this many days. 1 means a genuine day-over-day move."),
    min_baseline_days: int = Query(2, ge=1, le=8,
        description="rolling_7d mode: require at least this many snapshots in "
                    "the 7-day window. 1 point is not a baseline."),
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
        # max_gap is the whole point of this mode. A skill absent from a
        # snapshot has no row that day, so its "previous" point can be weeks
        # back: on one measured date 204 skills had a true one-day comparison
        # while a tail ran to 4, 6, 14 and 24 days. Ranking a 24-day drift
        # against a one-day move is not a comparison, it is a longer clock.
        sql = """
            SELECT skill, snapshot_date, posting_count,
                   previous_count::float, change::float, pct_change::float,
                   'previous_day' AS comparison,
                   previous_date, days_since_previous,
                   NULL::int AS baseline_days
            FROM skill_delta_daily
            WHERE snapshot_date = %s AND change IS NOT NULL
              AND days_since_previous <= %s
        """
        order_col = "change"
        params = [target, max_gap]
    else:
        # A seven-calendar-day window averages only the snapshots inside it,
        # which across a gap can be one point. One point is not a baseline.
        sql = """
            SELECT skill, snapshot_date, posting_count,
                   baseline_7d::float        AS previous_count,
                   change_vs_baseline::float AS change,
                   pct_vs_baseline::float    AS pct_change,
                   'rolling_7d' AS comparison,
                   NULL::date AS previous_date, NULL::int AS days_since_previous,
                   baseline_days
            FROM skill_delta_vs_baseline
            WHERE snapshot_date = %s AND baseline_days >= %s
        """
        order_col = "change_vs_baseline"
        params = [target, min_baseline_days]

    if skill:
        sql += " AND skill = ANY(%s)"
        params.append(list(skill))

    if direction == "up":
        sql += f" AND {order_col} > 0"
        ordering = f"ORDER BY {order_col} DESC"
    elif direction == "down":
        sql += f" AND {order_col} < 0"
        ordering = f"ORDER BY {order_col} ASC"
    else:
        ordering = f"ORDER BY ABS({order_col}) DESC"

    params.append(limit)
    rows = fetch_all(f"{sql} {ordering} LIMIT %s", tuple(params))

    # Annotate rather than filter. A step caused by the instrument is still a
    # real recorded change; hiding it would be its own distortion. What a
    # reader needs is to know which kind they are looking at, because a skill
    # that was previously invisible steps from zero and can top this ranking
    # without any employer having changed anything.
    changes = fetch_all(
        "SELECT changed_on, summary FROM instrument_changes ORDER BY changed_on", ())
    for row in rows:
        # previous_day carries its own interval; rolling_7d spans seven days.
        start = row.get("previous_date") or (row["snapshot_date"] - timedelta(days=7))
        crossed = [c for c in changes if start < c["changed_on"] <= row["snapshot_date"]]
        if crossed:
            row["crosses_instrument_change"] = True
            row["instrument_change_note"] = " | ".join(
                f"{c['changed_on']}: {c['summary']}" for c in crossed)
    return rows


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
        SELECT skill, first_seen, days_present, days_since_first_seen
        FROM skill_first_appearances
        {clause}
        ORDER BY first_seen DESC, skill
        LIMIT %s
    """, tuple(params))
