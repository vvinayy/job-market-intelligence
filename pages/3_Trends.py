"""Trends — how demand shifts over time. Needs accumulated history."""

from datetime import date, timedelta

import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Trends", layout="wide", page_icon="◎")
st.title("Trends over time")

coverage = dc.trends_coverage()
days = int(coverage.get("days_recorded") or 0)

missing = int(coverage.get("days_missing") or 0)

c1, c2, c3 = st.columns(3)
c1.metric("Days recorded", days,
          help="Snapshots taken. A snapshot only happens when the machine is on.")
c2.metric("Earliest", coverage.get("earliest") or "—")
c3.metric("Skills tracked", int(coverage.get("distinct_skills") or 0))

if missing:
    st.caption(
        f"**{missing} day(s) in that span were never snapshotted** and cannot be "
        f"recovered — {days} recorded across "
        f"{int(coverage.get('calendar_days_spanned') or 0)} calendar days. "
        "Comparisons below step over those gaps, so each one carries the interval "
        "it actually covers."
    )

st.divider()

if days < 2:
    st.info(
        f"**{days} day of history so far.** A trend needs at least two points to "
        "compare. Day-over-day views unlock at 2 days; the rolling-baseline "
        "comparison needs 8. History grows one day per scheduled run."
    )
    st.stop()


tab0, tab1, tab2, tab3 = st.tabs(["Weekly digest", "Demand over time", "Recent movers", "New arrivals"])


with tab0:
    st.write("A templated summary of the last 7 days — not AI-written prose, just the same numbers the other tabs show, composed into sentences.")

    facts = dc.summary()
    new_postings = int(facts.get("new_postings_7d") or 0)
    active = int(facts.get("active_last_7_days") or 0)
    still_open = int(facts.get("still_open") or 0)
    closed = int(facts.get("closed") or 0)

    movers, mode = dc.movers(limit=3)
    # Asked for by date, not sliced out of a limited page. The old form fetched
    # 40 rows and filtered them, so the digest reported the page size as a
    # count -- "40 skills" when 121 qualified. The limit here is the endpoint
    # ceiling, and saturation is stated rather than hidden.
    NEW_SKILL_CAP = 500
    new_this_week = dc.new_skills(
        limit=NEW_SKILL_CAP,
        since=str(date.today() - timedelta(days=7)))
    top_skill = dc.skill_demand(limit=1)

    d1, d2, d3 = st.columns(3)
    d1.metric("New postings", new_postings, help="First seen in the last 7 days")
    d2.metric("Seen again this week", active,
              help="Any posting a search surfaced in the last 7 days, new or old. "
                   "A coverage figure: it says what we scraped, not what is open.")
    saturated = len(new_this_week) >= NEW_SKILL_CAP
    d3.metric("New skills spotted",
              f"{NEW_SKILL_CAP}+" if saturated else len(new_this_week),
              help="First recorded in the last 7 calendar days.")

    st.markdown("#### This week, in sentences")
    # The old second half read "of which N are still showing up", which was
    # wrong twice: active_last_7_days counts the whole table, so it is not a
    # subset of the new postings and was routinely the larger number, and
    # "showing up" is scrape coverage rather than liveness. The liveness
    # columns answer what the sentence was reaching for.
    lines = [
        f"**{new_postings} new postings** were collected this week. "
        f"Of everything collected so far, **{still_open} are confirmed still open** "
        f"and **{closed} have closed**."
    ]

    if not movers.empty:
        biggest = movers.sort_values("change", key=abs, ascending=False).iloc[0]
        direction = "up" if biggest["change"] > 0 else "down"
        if mode == "rolling_7d":
            comparison = (f"vs. its 7-day average "
                          f"(built from {int(biggest['baseline_days'])} snapshot(s))")
        else:
            gap = int(biggest["days_since_previous"])
            comparison = "vs. yesterday" if gap == 1 else f"vs. {gap} days earlier"
        lines.append(f"The biggest mover was **{biggest['skill']}**, {direction} {abs(biggest['change']):.0f} postings {comparison}.")

    if not new_this_week.empty:
        names = ", ".join(new_this_week["skill"].head(5).tolist())
        more = len(new_this_week) - 5
        suffix = f", and {more} more" if more > 0 else ""
        count = (f"at least {NEW_SKILL_CAP}" if saturated
                 else str(len(new_this_week)))
        lines.append(f"**{count} skill(s)** were recorded for the first time this week: {names}{suffix}.")

    if not top_skill.empty:
        lines.append(f"Overall, **{top_skill.iloc[0]['skill']}** remains the single most requested skill across every posting collected so far.")

    for line in lines:
        st.markdown(f"- {line}")

    st.caption("How demand has moved since daily tracking began. Pick skills to "
               "chart below, or see which rose and fell fastest.")


with tab1:
    top = dc.skill_demand(limit=40)
    tracked = top["skill"].tolist() if not top.empty else []
    chosen = st.multiselect("Skills to plot", tracked)
    st.caption("Listed most in-demand first — nothing is pre-selected, pick what you want to compare.")

    if chosen:
        series = dc.skill_series(chosen)
        if not series.empty:
            fig = px.line(series, x="snapshot_date", y="posting_count", color="skill", markers=True)
            fig.update_layout(height=460, margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title=None, yaxis_title="postings", **dc.TRANSPARENT)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Pick one or more skills above to plot their demand over time.")


with tab2:
    chosen_movers = st.multiselect("Skills to see", tracked, key="movers_skills")
    st.caption("Listed most in-demand first — leave empty for the biggest movers overall, or pick specific skills to track.")

    data, mode = dc.movers(
        limit=len(chosen_movers) if chosen_movers else 25,
        skills=chosen_movers or None,
    )

    if mode == "previous_day":
        st.warning(
            f"The rolling-baseline comparison needs 8 days of history; {days} recorded. "
            "Showing simple day-over-day change instead — noisier, since a skill can "
            "move purely from different postings appearing in that day's sample."
        )
        label = "change vs the previous day"
    else:
        label = "change vs 7-day average"

    if data.empty:
        st.info("No movement data yet for those skills." if chosen_movers else "Nothing has moved yet.")
    else:
        fig = px.bar(
            data.sort_values("change"), x="change", y="skill", orientation="h",
            color="change", color_continuous_scale=["#e05c5c", "#6b7280", "#c8d400"],
            color_continuous_midpoint=0,
        )
        fig.update_layout(height=max(400, len(data) * 22),
                          margin=dict(l=0, r=20, t=10, b=0), coloraxis_showscale=False,
                          xaxis_title=label, yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)

        # A step caused by the measuring reads exactly like a hiring surge, and
        # ranks at the top precisely because it is large. Said before the
        # caveats below, because it can invalidate the whole ranking rather
        # than qualify one row of it.
        if "crosses_instrument_change" in data and data["crosses_instrument_change"].any():
            affected = int(data["crosses_instrument_change"].sum())
            note = data.loc[data["crosses_instrument_change"], "instrument_change_note"].iloc[0]
            st.warning(
                f"**{affected} of these {len(data)} span a change to the measuring itself.** "
                "A skill the pipeline previously could not see records zero every day, "
                "then steps to its real level the moment it becomes visible — which "
                "looks identical to a surge in demand, and ranks near the top for the "
                "same reason. Treat these as the instrument moving, not the market.\n\n"
                f"{note}"
            )

        if mode == "previous_day":
            st.caption(
                "Only genuine one-day moves are shown. A skill missing from a "
                "snapshot has no row that day, so its previous point can sit weeks "
                "back — ranking that against a one-day move would just be giving it "
                "a longer clock."
            )
        else:
            thin = int((data["baseline_days"] < 3).sum())
            if thin:
                st.caption(
                    f"{thin} of these have a baseline built from fewer than 3 "
                    "snapshots — the window is 7 calendar days, but only the "
                    "snapshots inside it are averaged. See `baseline_days` below."
                )

        st.dataframe(data, use_container_width=True, hide_index=True)


with tab3:
    st.write(
        "Skills recorded for the first time. Often the most interesting signal — "
        "something nobody was asking for last week."
    )
    fresh = dc.new_skills(limit=40)
    if fresh.empty:
        st.info("No new skills recorded yet.")
    else:
        st.dataframe(fresh, use_container_width=True, hide_index=True, height=440)
        st.caption(
            "A first appearance can mean genuinely new demand, or simply that a search "
            "happened to surface a posting using that term for the first time."
        )

dc.sampling_note()
