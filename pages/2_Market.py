"""Market — roles, employers, locations. Ranked bars throughout."""

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
    closures = dc.closures(dimension=dimension)

    if closures.empty:
        st.info("No closure data yet — postings need to be checked for a few days first.")
    else:
        baseline = (100 * closures["closed"].sum()
                    / (closures["postings"] * closures["mean_exposure_days"]).sum())
        closures = closures.sort_values("per_100_posting_days")

        fig = px.bar(closures, x="per_100_posting_days", y="bucket", orientation="h",
                     text=closures["per_100_posting_days"].map(lambda v: f"{v:.2f}"),
                     color="per_100_posting_days", color_continuous_scale=dc.SCALE,
                     custom_data=["postings", "closed", "pct_closed", "mean_exposure_days"])
        fig.update_traces(
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br>%{customdata[1]} of %{customdata[0]} postings closed "
                          "(%{customdata[2]}%)<br>watched %{customdata[3]} days on "
                          "average<extra></extra>")
        # A rate means nothing on its own -- the line is what makes a bar
        # readable as faster or slower than the market.
        fig.add_vline(x=baseline, line_dash="dot", line_color="#9aa5a2",
                      annotation_text="all postings", annotation_position="top")
        fig.update_layout(height=max(300, len(closures) * 46),
                          margin=dict(l=0, r=60, t=26, b=0), coloraxis_showscale=False,
                          yaxis_title=None,
                          xaxis_title="closures per 100 days a posting is listed",
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "How fast postings stop being listed, measured against how long each "
            "one has been tracked — a posting found two weeks ago has had twice as "
            "long to close as one found last week, so plain counts would mostly "
            "reflect when it was collected. Naukri only says a posting is gone, "
            "never whether anyone was hired."
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
            "postings that state a requirement at all — fewer than most charts here, "
            "since this field was added to the scraper later."
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
            "produces a Hyderabad-shaped chart — this is not national distribution."
        )

        st.divider()
        st.subheader("Postings by state")
        states = dc.states_reference()
        if not states.empty:
            states = states.rename(columns={"state_name": "state"})
            st.plotly_chart(ranked_bar(states, "postings", "state"), use_container_width=True)


dc.sampling_note()
