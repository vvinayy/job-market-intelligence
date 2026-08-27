"""Composition — the employer's own labels: industry, department, role
category, and the degrees they'll accept.

Every other page reads the posting the way a candidate would: what skills,
what experience, which city. This page reads the tags Naukri asks the
recruiter to pick, which is a different and sometimes contradictory view of
the same job.
"""

import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Composition", layout="wide", page_icon="◎")
st.title("Composition")
st.caption("How employers classify their own postings — and what they'll accept as qualifications.")


def ranked_bar(data, value_col, label_col, height_per_row=28, min_height=280):
    fig = px.bar(
        data.sort_values(value_col), x=value_col, y=label_col, orientation="h",
        text=value_col, color=value_col, color_continuous_scale=dc.SCALE,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(min_height, len(data) * height_per_row),
        margin=dict(l=0, r=44, t=10, b=0), coloraxis_showscale=False,
        xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT,
    )
    return fig


facts = dc.summary()
total = int(facts.get("total_postings") or 0)


def coverage_note(covered: int, what: str) -> None:
    """These tags were added to the scraper partway through, so older postings
    carry none of them. Stating the base stops a reader treating the bars as a
    share of everything collected."""
    if not total or not covered:
        return
    st.caption(
        f"Drawn from the {covered:,} of {total:,} postings that carry a {what} — "
        "this field was added to the scraper partway through, so postings "
        "collected before then have none. Every posting scraped recently does."
    )


tab1, tab2 = st.tabs(["Who is hiring", "Qualifications accepted"])


with tab1:
    st.subheader("Industry")
    industries = dc.industry_types()

    if industries.empty:
        st.info("No industry data yet.")
    else:
        industries = industries.rename(columns={"name": "industry"})
        shown = industries[industries["postings"] > 0]
        st.plotly_chart(ranked_bar(shown, "postings", "industry"), use_container_width=True)
        coverage_note(int(shown["postings"].sum()), "industry tag")

    st.divider()
    st.subheader("Department")
    departments = dc.departments()

    if departments.empty:
        st.info("No department data yet.")
    else:
        departments = departments.rename(columns={"name": "department"})
        shown = departments[departments["postings"] > 0]
        st.plotly_chart(ranked_bar(shown, "postings", "department"), use_container_width=True)
        st.caption(
            "The broadest grouping Naukri uses — a handful of departments cover "
            "everything, so this is a shape-of-the-whole view rather than a way "
            "to tell similar roles apart."
        )

    st.divider()
    st.subheader("Role category")
    categories = dc.role_categories()

    if categories.empty:
        st.info("No role category data yet.")
    else:
        categories = categories.rename(columns={"name": "role category"})
        shown = categories[categories["postings"] > 0]
        st.plotly_chart(ranked_bar(shown, "postings", "role category"), use_container_width=True)
        st.caption(
            "Naukri's own classification, chosen by the recruiter from a dropdown "
            "rather than read from the job description. It disagrees with the role "
            "we derive from the title on about a third of postings, and sometimes "
            "contradicts the description outright — useful as a second opinion, "
            "not as the answer."
        )


with tab2:
    st.subheader("Degrees employers will accept")
    degrees = dc.education_degrees()

    if degrees.empty:
        st.info("No degree data yet.")
    else:
        levels = ["UG", "PG", "Doctorate"]
        available = [lvl for lvl in levels if lvl in set(degrees["level"])]
        chosen = st.multiselect("Level", available, default=available, key="comp_levels")
        shown = degrees[degrees["level"].isin(chosen) & (degrees["postings"] > 0)]

        if shown.empty:
            st.info("No degrees at the selected level(s).")
        else:
            shown = shown.assign(degree=shown["name"] + "  (" + shown["level"] + ")")
            st.plotly_chart(ranked_bar(shown, "postings", "degree"),
                            use_container_width=True)
            st.caption(
                "A posting listing several degrees will accept **any one** of them — "
                "these are alternatives, not a stack of requirements, so they don't "
                "sum to the number of postings. Naukri writes some as a pair "
                "(\"B.Tech / B.E.\"); those are split into one row each here."
            )

    st.divider()
    st.subheader("Fields of study")
    specs = dc.education_specializations()

    if specs.empty:
        st.info("No specialization data yet.")
    else:
        specs = specs.rename(columns={"name": "specialization"})
        n = st.slider("How many to show", 5, 40, 20, key="comp_spec_n")
        shown = specs[specs["postings"] > 0].nlargest(n, "postings")
        st.plotly_chart(ranked_bar(shown, "postings", "specialization"),
                        use_container_width=True)
        st.caption(
            "Whether employers actually want a computer science degree or simply "
            "an engineering one. **\"Any Specialization\" is a real answer** and is "
            "shown as its own bar rather than dropped — a posting that says it is "
            "telling you the field does not matter."
        )

dc.sampling_note()
