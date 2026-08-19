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
                f"{int(other)} postings didn't match a known role pattern and are "
                "grouped as 'Other' — job titles are free text, so this is expected."
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
    st.subheader("Seniority mix")
    seniority = dc.seniority_distribution()

    if seniority.empty:
        st.info("No titles with a seniority marker yet.")
    else:
        order = ["Intern/Trainee", "Junior", "Associate", "Senior", "Lead/Principal", "Manager/Leadership"]
        seniority["bucket"] = seniority["bucket"].astype("category").cat.set_categories(order, ordered=True)
        seniority = seniority.sort_values("bucket", ascending=False)

        base = int(seniority["postings"].sum())
        fig = px.bar(seniority, x="postings", y="bucket", orientation="h",
                     text="postings", color="postings", color_continuous_scale=dc.SCALE)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=280, margin=dict(l=0, r=40, t=10, b=0),
                          coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Based on the {base} postings whose title actually carried a seniority word "
            "(\"Senior\", \"Lead\", \"Manager\", ...) — most titles don't, so this is a much "
            "smaller base than the total postings count, not a full breakdown of every posting."
        )

    st.divider()
    st.subheader("Work arrangement")
    wt = dc.working_types()

    if wt.empty:
        st.info("No arrangement data yet.")
    else:
        st.plotly_chart(ranked_bar(wt, "postings", "name", height_per_row=40, min_height=180),
                        use_container_width=True)
        st.caption(
            "Naukri only shows a work-mode badge on Hybrid and Remote postings — a "
            "posting with no badge is On-site by Naukri's own convention, not an unknown."
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
            f"Based on the {base} postings that disclose an education requirement so far — "
            "a much smaller, newer slice of the data than most other charts here. A posting "
            "can require more than one level (e.g. both UG and PG), so shares don't sum to 100%."
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
