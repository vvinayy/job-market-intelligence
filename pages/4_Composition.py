"""Composition — which kinds of employer are hiring, and what qualifications
they'll accept.

Every posting collected is an IT role: the searches only ever ask for
developers, data scientists and ML engineers. So the industry breakdown is
not "which jobs did we find" — it is which *sectors* hire IT people, which is
a different and more interesting question. A pharma company hiring a data
scientist is still a data science job.

Naukri's Role Category is deliberately not charted here. It is a dropdown the
recruiter picks, and it is wrong often enough to mislead: "Forward Deployed
Engineer" filed under BD / Pre Sales, "Python Software Developer" under BFSI.
The Market page derives role from the job title instead, which is the more
reliable of the two, and showing both would only invite the question of which
to believe.
"""

import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Composition", layout="wide", page_icon="◎")
st.title("Composition")
st.caption("Which sectors are hiring IT roles, and what qualifications they ask for.")

# Below this a bar carries no information: a sector with one posting tells you
# about that posting, not about the sector. The excluded ones are still
# counted, in a line under each chart, so nothing silently disappears.
MIN_POSTINGS = 5


def ranked_bar(data, label_col, height_per_row=30, min_height=260):
    fig = px.bar(
        data.sort_values("postings"), x="postings", y=label_col, orientation="h",
        text="postings", color="postings", color_continuous_scale=dc.SCALE,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(min_height, len(data) * height_per_row),
        margin=dict(l=0, r=46, t=10, b=0), coloraxis_showscale=False,
        xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT,
    )
    return fig


def split_tail(data, min_postings=MIN_POSTINGS):
    """Categories big enough to chart, and the long tail that isn't.

    Returned separately rather than collapsed into an "Other" bar — a bar
    labelled Other reads as a category, and here it would sit next to a real
    category that Naukri actually calls "Other".
    """
    present = data[data["postings"] > 0]
    major = present[present["postings"] >= min_postings]
    minor = present[present["postings"] < min_postings]
    return major, minor


def tail_note(minor, noun: str) -> None:
    if minor.empty:
        return
    st.caption(
        f"{len(minor)} smaller {noun} are left out, "
        f"{int(minor['postings'].sum())} postings between them — "
        f"each has fewer than {MIN_POSTINGS}, which is too few to read anything from."
    )


tab1, tab2 = st.tabs(["Who is hiring", "Qualifications accepted"])


with tab1:
    st.subheader("Which sectors hire IT roles")
    industries = dc.industry_types()

    if industries.empty:
        st.info("No industry data yet.")
    else:
        industries = industries.rename(columns={"name": "sector"})
        major, minor = split_tail(industries)

        if major.empty:
            st.info("No sector has enough postings to chart yet.")
        else:
            st.plotly_chart(ranked_bar(major, "sector"), use_container_width=True)
            st.caption(
                "This is the **employer's** industry, not the type of job — every "
                "posting collected is an IT role. Read it as where IT hiring is "
                "happening: mostly at IT services firms, but a real share of it "
                "inside pharma, finance and logistics companies."
            )
            tail_note(minor, "sectors")

    st.divider()
    st.subheader("Which function the role sits in")
    departments = dc.departments()

    if departments.empty:
        st.info("No department data yet.")
    else:
        departments = departments.rename(columns={"name": "department"})
        major, minor = split_tail(departments)

        if major.empty:
            st.info("No department has enough postings to chart yet.")
        else:
            st.plotly_chart(ranked_bar(major, "department"), use_container_width=True)
            st.caption(
                "Naukri's own department tag, chosen by the recruiter. The split "
                "between engineering and data roles is reliable; the smaller "
                "entries are mostly mis-tagged IT jobs rather than genuinely "
                "different work."
            )
            tail_note(minor, "departments")


with tab2:
    st.subheader("Degrees employers will accept")
    degrees = dc.education_degrees()

    if degrees.empty:
        st.info("No degree data yet.")
    else:
        levels = ["UG", "PG", "Doctorate"]
        available = [lvl for lvl in levels if lvl in set(degrees["level"])]
        chosen = st.multiselect("Level", available, default=available, key="comp_levels")

        at_level = degrees[degrees["level"].isin(chosen)]
        major, minor = split_tail(at_level)

        if major.empty:
            st.info("No degrees at the selected level(s) have enough postings to chart.")
        else:
            major = major.assign(degree=major["name"] + "  (" + major["level"] + ")")
            st.plotly_chart(ranked_bar(major, "degree"), use_container_width=True)
            st.caption(
                "A posting listing several degrees will accept **any one** of them — "
                "these are alternatives, not a stack of requirements, so they don't "
                "sum to the number of postings. Naukri writes some as a pair "
                "(\"B.Tech / B.E.\"); those are split into one row each here."
            )
            tail_note(minor, "degrees")

    st.divider()
    st.subheader("Fields of study")
    specs = dc.education_specializations()

    if specs.empty:
        st.info("No specialization data yet.")
    else:
        specs = specs.rename(columns={"name": "specialization"})
        # Not thresholded like the others: almost every posting says "Any
        # Specialization", so a cutoff would leave one bar and hide the point.
        n = st.slider("How many to show", 5, 40, 12, key="comp_spec_n")
        shown = specs[specs["postings"] > 0].nlargest(n, "postings")
        st.plotly_chart(ranked_bar(shown, "specialization"), use_container_width=True)
        st.caption(
            "Whether employers actually want a computer science degree or simply "
            "an engineering one. **\"Any Specialization\" dominates**, and that is "
            "the finding: most postings that mention education at all are saying "
            "the field does not matter. Named fields are named by very few."
        )

dc.sampling_note()
