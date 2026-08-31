"""Market — roles, employers, locations. Ranked bars throughout."""

from datetime import date

import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Market", layout="wide", page_icon="◎")
st.title("Market")


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


tab1, tab2, tab3 = st.tabs(["Roles", "Employers", "Locations"])


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

    st.divider()
    # Replaced the seniority mix chart, which could only speak for the ~20% of
    # postings whose title happened to carry "Senior"/"Lead"/"Manager".
    # experience_min is stated on 97%, so this axis covers nearly everything.
    st.subheader("How negotiable are the requirements?")
    flex = dc.flexibility_by_experience()

    if flex.empty:
        st.info("No experience bands with enough postings yet.")
    else:
        order = ["0-1 years", "2-3 years", "4-6 years", "7-10 years", "10+ years", "Not stated"]
        flex["bucket"] = flex["bucket"].astype("category").cat.set_categories(order, ordered=True)
        flex = flex.sort_values("bucket", ascending=False)

        fig = px.bar(flex, x="pct_offering_a_choice", y="bucket", orientation="h",
                     text=flex["pct_offering_a_choice"].map(lambda v: f"{v:.0f}%"),
                     color="pct_offering_a_choice", color_continuous_scale=dc.SCALE,
                     custom_data=["postings", "offering_a_choice"])
        fig.update_traces(
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{customdata[1]} of %{customdata[0]} postings "
                          "offer a choice<extra></extra>")
        fig.update_layout(height=280, margin=dict(l=0, r=48, t=10, b=0),
                          coloraxis_showscale=False, yaxis_title=None,
                          xaxis_title="% of postings offering an either/or skill",
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "The share of postings at each experience level that name alternatives — "
            "\"Angular or React\" rather than demanding both. Notably it does not rise "
            "with seniority: entry-level roles are the most rigid of all. Alternatives "
            "are read from the description wording, so trust the ordering more than "
            "the exact percentages."
        )

    # The "Work arrangement" chart was removed here. Naukri badges work mode on
    # only ~25% of postings, so the chart was three-quarters "Not stated" — a
    # picture of Naukri's badging, not of the market. /reference/working-types
    # still serves the counts if it is ever worth stating as a plain figure.

    st.divider()
    st.subheader("How quickly are these roles being filled?")
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

            fig = px.bar(shown, x="per_100_posting_days", y="label", orientation="h",
                         text=shown["per_100_posting_days"].map(lambda v: f"{v:.2f}"),
                         color="per_100_posting_days", color_continuous_scale=dc.SCALE,
                         custom_data=["postings", "closed", "pct_closed", "mean_exposure_days"])
            fig.update_traces(
                textposition="outside", cliponaxis=False,
                marker_pattern_shape=["/" if b in thin else "" for b in shown["bucket"]],
                hovertemplate="%{y}<br>%{customdata[1]} of %{customdata[0]} postings closed "
                              "(%{customdata[2]}%)<br>watched %{customdata[3]} days on "
                              "average<extra></extra>")
            # A rate means nothing on its own -- the line is what makes a bar
            # readable as faster or slower than the market.
            fig.add_vline(x=baseline, line_dash="dot", line_color="#9aa5a2",
                          annotation_text="all postings", annotation_position="top")
            fig.update_layout(height=max(300, len(shown) * 46),
                              margin=dict(l=0, r=60, t=26, b=0), coloraxis_showscale=False,
                              yaxis_title=None,
                              xaxis_title="closures per 100 days a posting is under observation",
                              **dc.TRANSPARENT)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"Closures per 100 days a posting spends **under observation**, "
                f"counted only from {started} — the day closure checking began. "
                "Days before that are not exposure: nothing was checking, so no "
                "closure could have been seen. Postings already expired when "
                "checking started are excluded entirely; they died at unknown "
                "earlier dates. The dotted line is every posting observed, not "
                "just the roles selected. Naukri only says a posting is gone, "
                "never whether anyone was hired."
            )
            closed_shown = int(shown["closed"].sum())
            st.info(
                f"**Treat the ordering as provisional.** The observation window is "
                f"{window_days} days old and holds {closed_shown} closure(s) across "
                f"{len(shown)} group(s) — a handful of events per bar, which is "
                "not enough to separate them. It sharpens as the window lengthens."
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


dc.sampling_note()
