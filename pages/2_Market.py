"""Market — roles, employers, locations, and how new postings arrive over time."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

import dash_common as dc

st.set_page_config(page_title="Market", layout="wide", page_icon="◎")
st.title("Market")
dc.freshness_note()


def ranked_bar(data, value_col, label_col, height_per_row=26, min_height=300, x_title=None):
    fig = px.bar(
        data.sort_values(value_col), x=value_col, y=label_col, orientation="h",
        text=value_col, color=value_col, color_continuous_scale=dc.SCALE,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(min_height, len(data) * height_per_row),
        margin=dict(l=0, r=40, t=10, b=0), coloraxis_showscale=False,
        xaxis_title=x_title, yaxis_title=None, **dc.TRANSPARENT,
    )
    return fig


# Closure rates below this many postings turn on one or two events: Business
# Analyst ranks top of the whole market on 4 postings and 3 closures. Groups
# under it stay selectable -- hiding data is its own distortion -- but they are
# marked, never ranked silently beside a bar built on 139.
RELIABLE_POSTINGS = 15

# Not roles, but what is left when a title matches no pattern -- "Other" from
# classify_role(), "Uncategorised" for a NULL. Offering them for comparison
# invites reading a residual as a peer of ML Engineer when it is really a mixed
# bag of titles with nothing in common. They stay inside the baseline, since
# they are real postings that really closed; they just cannot be a bar.
RESIDUAL_BUCKETS = {"Other", "Uncategorised"}


tab1, tab2, tab3, tab4 = st.tabs(["Roles", "Employers", "Locations", "Over time"])


with tab1:
    st.subheader("Which roles are being hired for")
    roles = dc.role_distribution()

    if roles.empty:
        st.info("No postings yet.")
    else:
        st.plotly_chart(ranked_bar(roles, "postings", "role"), use_container_width=True)
        other = roles[roles["role"] == "Other"]["postings"].sum()
        if other:
            st.caption(
                "Roles are read from job titles, which employers write freely. "
                f"{int(other)} titles matched no known pattern and sit in 'Other'."
            )

    st.divider()
    st.subheader("Experience level asked for")
    bands = dc.experience_distribution()

    if not bands.empty:
        order = ['0-1 years', '2-3 years', '4-6 years', '7-10 years', '10+ years', 'Not stated']
        bands["bucket"] = bands["bucket"].astype("category").cat.set_categories(order, ordered=True)
        bands = bands.sort_values("bucket", ascending=False)

        fig = px.bar(bands, x="postings", y="bucket", orientation="h",
                     text="postings", color="postings", color_continuous_scale=dc.SCALE)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=320, margin=dict(l=0, r=40, t=10, b=0),
                          coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)

    # The "How negotiable are the requirements?" chart was removed here. It
    # measured description length more than negotiability: alternatives are
    # detected from sentence structure, so a short posting has nowhere to put
    # one. Zero of the 47 postings under 500 characters register a choice,
    # rising to 55% over 3,000 — and each band's score tracked its average
    # description length, with 0-1 years shortest (2,075 chars) and lowest,
    # 10+ years longest (3,592) and highest. Controlling for length flattens
    # every band above entry level to ~50%, so the chart's shape was mostly an
    # artifact. /analytics/flexibility-by-experience still serves the figures.

    # The "Work arrangement" chart was removed here. Naukri badges work mode on
    # only ~25% of postings, so the chart was three-quarters "Not stated" — a
    # picture of Naukri's badging, not of the market. /reference/working-types
    # still serves the counts if it is ever worth stating as a plain figure.

    st.divider()
    # "Close", not "filled": Naukri only says a posting is gone, never whether
    # anyone was hired.
    st.subheader("How quickly do these postings close?")
    dim_label = st.radio("Compare by", ["Experience level", "Role"],
                         horizontal=True, key="closure_dim")
    dimension = "experience_band" if dim_label == "Experience level" else "role_family"
    # min_postings=1 so every group comes back and the page decides what to
    # show. The baseline must stay the whole market, so it is computed before
    # any selection is applied; filtering in the query instead would move the
    # reference line with every change and no bar could read as above or below
    # average.
    closures = dc.closures(dimension=dimension, min_postings=1)

    if closures.empty:
        st.info("No closure data yet — postings need to be checked for a few days first.")
    else:
        baseline = (100 * closures["closed"].sum()
                    / (closures["postings"] * closures["mean_exposure_days"]).sum())
        thin = set(closures.loc[closures["postings"] < RELIABLE_POSTINGS, "bucket"])
        # The window, for the caption. Its length comes from the recorded start
        # date, not from mean exposure -- exposure averages in postings first
        # seen after checking began and would understate how long we have been
        # watching.
        started = dc.summary().get("liveness_started_on")
        window_days = ((date.today() - date.fromisoformat(str(started))).days
                       if started else None)

        if dimension == "role_family":
            # Options come from the closure data, not /reference/roles: a role
            # with no checked postings has no rate to chart, and offering it
            # would only ever return an empty bar.
            options = [b for b in closures.sort_values("postings", ascending=False)["bucket"]
                       if b not in RESIDUAL_BUCKETS]
            default = [b for b in options if b not in thin] or options
            chosen = st.multiselect(
                "Roles to compare", options, default=default, key="closure_roles",
                help=f"Starts with roles of at least {RELIABLE_POSTINGS} postings, "
                     "ranked fastest first. Smaller ones can be added and are "
                     "hatched on the chart.")
            closures = closures[closures["bucket"].isin(chosen)]
        # Experience is six ordered bands that all carry weight. Letting them be
        # dropped would only break the ordering that makes that chart readable.

        if closures.empty:
            st.info("Pick at least one role to compare.")
        else:
            shown = closures.sort_values("per_100_posting_days").copy()
            shown["label"] = [
                f"{b}  ({n} postings)" if b in thin else b
                for b, n in zip(shown["bucket"], shown["postings"])
            ]
            # Two plain readings of the same rate (closures per 100 posting-days).
            # Per week is only a rescale, so the bar keeps it. Days open is its
            # inverse, which assumes a steady closing pace -- so it is the label,
            # not the bar, and the caption says so.
            shown["per_week"] = shown["per_100_posting_days"] * 7
            shown["days_open"] = [100 / r if r > 0 else None
                                  for r in shown["per_100_posting_days"]]
            shown["text"] = [
                f"{w:.0f} a week · ~{d:.0f} days" if pd.notna(d) else f"{w:.0f} a week"
                for w, d in zip(shown["per_week"], shown["days_open"])
            ]
            shown["days_hover"] = [f"~{d:.0f} days" if pd.notna(d) else "(none closed yet)"
                                   for d in shown["days_open"]]

            fig = px.bar(shown, x="per_week", y="label", orientation="h",
                         text="text", color="per_week", color_continuous_scale=dc.SCALE,
                         custom_data=["postings", "closed", "pct_closed",
                                      "mean_exposure_days", "days_hover"])
            fig.update_traces(
                # Inside the bar where it fits, so the dotted average line does not
                # run through a label; hatched bars keep theirs outside, where it
                # stays legible.
                textposition=["outside" if b in thin else "auto" for b in shown["bucket"]],
                insidetextanchor="end", cliponaxis=False,
                marker_pattern_shape=["/" if b in thin else "" for b in shown["bucket"]],
                hovertemplate="%{y}<br>about %{x:.0f} of every 100 open postings close in a week"
                              "<br>a typical posting stays open %{customdata[4]}"
                              "<br>%{customdata[1]} of %{customdata[0]} postings closed so far "
                              "(%{customdata[2]}%), each watched %{customdata[3]} days on "
                              "average<extra></extra>")
            # A rate means nothing on its own -- the line is what makes a bar
            # readable as faster or slower than the market.
            fig.add_vline(x=baseline * 7, line_dash="dot", line_color="#9aa5a2", layer="below",
                          annotation_text=f"all postings: {baseline * 7:.0f} a week · "
                                          f"~{100 / baseline:.0f} days" if baseline else "all postings",
                          annotation_position="top")
            fig.update_layout(height=max(300, len(shown) * 46),
                              margin=dict(l=0, r=150, t=26, b=0), coloraxis_showscale=False,
                              yaxis_title=None,
                              xaxis_title="out of every 100 open postings, how many close in a week",
                              **dc.TRANSPARENT)
            st.plotly_chart(fig, use_container_width=True)
            # Explain with the fastest group that has enough postings -- a hatched
            # bar is the one reading the chart warns against.
            solid = shown[~shown["bucket"].isin(thin)]
            top = (solid if not solid.empty else shown).iloc[-1]
            example = (f"*\"{top['per_week']:.0f} a week\"* for {top['bucket']} means "
                       f"that out of every 100 such postings that are open, about "
                       f"{top['per_week']:.0f} close in a typical week. ")
            if pd.notna(top["days_open"]):
                example += (f"*\"~{top['days_open']:.0f} days\"* is what that works "
                            "out to for one posting — an estimate that assumes they "
                            "keep closing at the same pace. ")
            st.caption(
                "**How to read it:** a longer bar means postings of that kind close "
                "faster. " + example + f"Counted from {started}, when "
                "daily checking began, and Naukri only (hirist never says when a "
                "posting closes). The dotted line is all postings. \"Closed\" means "
                "Naukri took the posting down — it does not tell us whether anyone "
                "was hired."
            )
            closed_shown = int(shown["closed"].sum())
            st.info(
                f"**Treat the ordering as provisional.** {window_days} days of "
                f"checking so far: {closed_shown} closure(s) across {len(shown)} "
                f"group(s), about {closed_shown / len(shown):.0f} per group. Bars a "
                "few postings a week apart can still differ by chance; this "
                "sharpens as more days are checked."
            )

            charted_thin = [b for b in shown["bucket"] if b in thin]
            if charted_thin:
                st.warning(
                    f"**{', '.join(charted_thin)}** "
                    f"{'has' if len(charted_thin) == 1 else 'have'} fewer than "
                    f"{RELIABLE_POSTINGS} postings, so the rate rests on one or two "
                    "closures and can top the chart by chance. Hatched bars mark them."
                )

    st.divider()
    st.subheader("Education requirements")
    quals = dc.qualification_distribution()

    if quals.empty:
        st.info("No education data yet — this field was added recently, so only postings scraped since then carry it.")
    else:
        base = int(dc.summary().get("postings_with_education") or 0)
        fig = px.bar(quals.sort_values("postings"), x="postings", y="bucket", orientation="h",
                     text="postings", color="postings", color_continuous_scale=dc.SCALE)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=280, margin=dict(l=0, r=40, t=10, b=0),
                          coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "The degree levels employers ask for. A posting can accept more than one "
            f"(both UG and PG, say), so these don't sum to 100%. Drawn from the {base} "
            "postings that state a requirement at all — and that group is not a random "
            "sample: the scraper began capturing this field on 18 Aug, so coverage runs "
            "43% before that date and 100% after. The breakdown therefore leans toward "
            "recently collected postings."
        )


with tab2:
    st.subheader("Who is posting the most")
    n = st.slider("Companies to show", 5, 40, 20, key="emp_n")
    comps = dc.companies(limit=n)

    if comps.empty:
        st.info("No employer data yet.")
    else:
        st.plotly_chart(ranked_bar(comps, "postings", "company"), use_container_width=True)

    st.divider()
    st.subheader("Vacancies advertised per posting")
    openings = dc.openings_distribution()

    if openings.empty:
        st.info("No openings data yet.")
    else:
        openings["openings_n"] = openings["bucket"].astype(int)
        common = openings[openings["openings_n"] <= 20]
        overflow = openings[openings["openings_n"] > 20]

        fig = px.bar(common, x="openings_n", y="postings", text="postings",
                     color="postings", color_continuous_scale=dc.SCALE)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0),
                          coloraxis_showscale=False, xaxis_title="vacancies in one posting",
                          yaxis_title="postings", **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)

        if not overflow.empty:
            st.caption(
                f"Chart covers postings advertising up to 20 vacancies. A further "
                f"{int(overflow['postings'].sum())} advertise more, the largest listing "
                f"{int(overflow['openings_n'].max()):,} — including them would compress "
                "everything else into a single bar."
            )


with tab3:
    st.subheader("Where the postings are")
    cities = dc.cities_reference(with_postings_only=True)

    if cities.empty:
        st.info("No location data yet.")
    else:
        cities = cities.rename(columns={"city_name": "city"})
        mapped = cities[cities["city"].isin(dc.CITY_COORDINATES)].copy()
        mapped["lat"] = mapped["city"].map(lambda c: dc.CITY_COORDINATES[c][0])
        mapped["lon"] = mapped["city"].map(lambda c: dc.CITY_COORDINATES[c][1])

        if not mapped.empty:
            fig = px.scatter_map(
                mapped, lat="lat", lon="lon", size="postings", color="postings",
                color_continuous_scale=dc.SCALE, size_max=45, zoom=3.6,
                center=dict(lat=22.5, lon=79.0), hover_name="city",
                hover_data={"lat": False, "lon": False, "postings": True},
            )
            fig.update_layout(
                map_style="open-street-map", height=480,
                margin=dict(l=0, r=0, t=0, b=0), coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Postings by city")
        st.plotly_chart(ranked_bar(cities, "postings", "city"), use_container_width=True)
        st.caption(
            "Reflects which cities the scraper searches. Searching only Hyderabad "
            "produces a Hyderabad-shaped chart — this is not national distribution. "
            "A posting can list several cities and is counted under each, so these "
            "bars deliberately add up to more than the number of postings."
        )

        st.divider()
        st.subheader("Postings by state")
        states = dc.states_reference()
        if not states.empty:
            states = states.rename(columns={"state_name": "state"})
            st.plotly_chart(ranked_bar(states, "postings", "state"), use_container_width=True)


with tab4:
    st.subheader("New postings over time")
    st.write("How many postings appeared for the first time each day or week, "
             "broken down by what you pick.")

    BY_OPTIONS = {"Role": "role", "Experience level": "experience", "City": "city",
                  "Company": "company", "Job board": "source"}
    PLURAL = {"Role": "Roles", "Experience level": "Experience levels", "City": "Cities",
              "Company": "Companies", "Job board": "Job boards"}
    # Leftover buckets are real postings but not a peer of the named groups, so
    # they are offered, never pre-selected.
    RESIDUAL_GROUPS = {"Other", "Not recorded", "All other employers", "No resolved city"}

    c1, c2, c3 = st.columns([3, 2, 2])
    by_label = c1.radio("Break down by", list(BY_OPTIONS), horizontal=True, key="arr_by")
    period_label = c2.radio("Group by", ["Weeks", "Days"], horizontal=True, key="arr_period",
                            help="Weeks run Monday to Sunday. About 17 new postings arrive "
                                 "a day in total, so one group's daily line is mostly 0-5.")
    measure = c3.radio("Show", ["Count", "Share"], horizontal=True, key="arr_measure",
                       help="Share = this group's percentage of all postings first seen in "
                            "that day or week, which evens out quiet days and failed scrapes.")
    by, period = BY_OPTIONS[by_label], ("week" if period_label == "Weeks" else "day")

    # Bounds come from an unfiltered call, so the range slider always spans all
    # collected days whatever is picked.
    bounds = pd.DataFrame(dc.arrivals(by=by, period="day").get("rows") or [])
    if bounds.empty:
        st.info("No postings collected yet.")
    else:
        first_day = pd.to_datetime(bounds["period_start"]).min().date()
        today = date.today()
        since, until = st.slider("Dates", min_value=first_day, max_value=today,
                                 value=(first_day, today), format="D MMM YYYY", key="arr_dates")

        data = dc.arrivals(by=by, period=period, since=since, until=until)
        rows = pd.DataFrame(data.get("rows") or [])
        if rows.empty:
            st.info("No postings first appeared in those dates.")
        else:
            rows["period_start"] = pd.to_datetime(rows["period_start"]).dt.date
            ranking = rows.groupby("group")["postings"].sum().sort_values(ascending=False)
            options = ranking.index.tolist()
            default = [g for g in options if g not in RESIDUAL_GROUPS][:5]
            chosen = st.multiselect(f"{PLURAL[by_label]} to show", options, default=default,
                                    key=f"arr_groups_{by}",
                                    help="Ordered by postings in the selected dates.")

            if period == "day":
                axis = [since + timedelta(days=i) for i in range((until - since).days + 1)]
            else:
                start = since - timedelta(days=since.weekday())
                axis = [start + timedelta(weeks=i)
                        for i in range((until - start).days // 7 + 1)]
            no_scrape = {date.fromisoformat(d) for d in data.get("no_scrape_days") or []}
            partial = {date.fromisoformat(d) for d in data.get("partial_periods") or []}
            value = "postings" if measure == "Count" else "share_pct"

            fig = go.Figure()
            for i, g in enumerate(chosen):
                series = (rows[rows["group"] == g].set_index("period_start")[value]
                          .reindex(axis, fill_value=0).astype(float))
                # A day nothing was collected is a gap, not a zero: its postings
                # land on the next collected day instead.
                if period == "day":
                    series[[d for d in axis if d in no_scrape]] = None
                colour = dc.LINES[i % len(dc.LINES)]
                solid = series.where([d not in partial for d in axis])
                fig.add_trace(go.Scatter(
                    x=axis, y=solid, name=g, mode="lines+markers", legendgroup=g,
                    line=dict(color=colour), connectgaps=False,
                    hovertemplate=("%{x|%d %b %Y}" if period == "day" else "week of %{x|%d %b}")
                                  + f"<br>{g}: %{{y}}" + ("" if value == "postings" else "%")
                                  + "<extra></extra>"))
                if partial:
                    # Dashed and hollow into a partial week, so it never reads as a drop.
                    near = {d for p in partial for d in (p - timedelta(weeks=1), p,
                                                         p + timedelta(weeks=1))}
                    fig.add_trace(go.Scatter(
                        x=axis, y=series.where([d in near for d in axis]), legendgroup=g,
                        showlegend=False, mode="lines+markers", connectgaps=False,
                        line=dict(color=colour, dash="dot"),
                        marker=dict(symbol=["circle-open" if d in partial else "circle"
                                            for d in axis], size=9),
                        hovertemplate="week of %{x|%d %b} (partial)<br>" + g
                                      + ": %{y}<extra></extra>"))

            def on_axis(r: dict) -> tuple[date, date]:
                """A start-up range as the axis points it covers."""
                s, e = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
                if period == "week":
                    s, e = s - timedelta(days=s.weekday()), e - timedelta(days=e.weekday())
                return s, e

            startup_spans = [on_axis(r) for r in data.get("startup_ranges") or []]
            # datetime, not date: date minus 12 hours is the same date, which drew
            # a one-day start-up (18 Aug) as a zero-width band.
            half = timedelta(hours=12) if period == "day" else timedelta(days=3, hours=12)
            for s, e in startup_spans:
                s, e = datetime.combine(s, datetime.min.time()), datetime.combine(e, datetime.min.time())
                fig.add_vrect(x0=s - half, x1=e + half, fillcolor="#9aa5a2", opacity=0.15,
                              layer="below", line_width=0, annotation_text="start-up",
                              annotation_position="top left")
            fig.update_layout(height=460, margin=dict(l=0, r=0, t=30, b=0),
                              yaxis_title="new postings" if value == "postings"
                              else "% of that period's new postings",
                              xaxis_title=None, legend_title=None, **dc.TRANSPARENT)
            st.plotly_chart(fig, use_container_width=True)

            notes = ["Counted on the day a posting **first appeared in our searches**, "
                     "not the employer's posting date."]
            def span(r: dict) -> str:
                s, e = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
                if s == e:
                    return f"{s.day} {s:%b}"
                return (f"{s.day}–{e.day} {e:%b}" if s.month == e.month
                        else f"{s.day} {s:%b}–{e.day} {e:%b}")

            if data.get("startup_ranges"):
                notes.append("**Shaded = start-up:** "
                             + "; ".join(f"{span(r)} ({r['label'].lower()})"
                                         for r in data["startup_ranges"])
                             + ". Those spikes are postings that were already open, not a "
                               "hiring surge.")
            if partial and period == "week":
                notes.append("**Hollow points** are weeks only partly inside the dates, so "
                             "they read low.")
            if no_scrape and period == "day":
                notes.append(f"**Gaps** are the {len(no_scrape)} day(s) nothing was "
                             "collected; their postings show on the next day.")
            if by == "city":
                notes.append("A posting naming several cities counts in each, so shares "
                             "add up to more than 100%.")
            st.caption(" ".join(notes))

            st.markdown("**What arrived on a given " + ("day" if period == "day" else "week") + "**")
            periods = sorted(rows["period_start"].unique(), reverse=True)
            picked = st.selectbox(
                "Pick one", periods, key=f"arr_pick_{period}",
                format_func=lambda d: (d.strftime("%a %d %b %Y") if period == "day"
                                       else f"week of {d.strftime('%d %b %Y')}")
                                      + (" (partial)" if d in partial else "")
                                      + (" (start-up)" if any(s <= d <= e for s, e in startup_spans)
                                         else ""))
            that = rows[rows["period_start"] == picked].sort_values("postings", ascending=False)
            total_that = int(that["postings"].sum()) if by != "city" else None
            st.caption(f"{PLURAL[by_label]} by postings first seen then"
                       + (f" — {total_that} postings in all." if total_that else "."))
            st.plotly_chart(ranked_bar(that.head(15), "postings", "group"),
                            use_container_width=True)

            dc.csv_download(rows.rename(columns={"group": by_label.lower()}),
                            f"new_postings_by_{by}_{period}.csv")

dc.sampling_note()
