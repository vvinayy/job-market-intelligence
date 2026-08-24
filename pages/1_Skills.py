"""Skills — demand, pairings, seniority split, and category mix."""

import pandas as pd
import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Skills", layout="wide", page_icon="◎")
st.title("Skills")

tab1, tab2, tab5, tab3, tab4 = st.tabs(
    ["Demand", "Pairings", "Interchangeable", "By experience level", "By category"])


with tab1:
    n = st.slider("How many skills", 5, 50, 25, key="rank_n")
    data = dc.skill_demand(limit=n)

    if data.empty:
        st.info("No skill data yet.")
    else:
        fig = px.bar(
            data.sort_values("postings"), x="postings", y="skill", orientation="h",
            text="postings", color="postings", color_continuous_scale=dc.SCALE,
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(
            height=max(340, n * 24), margin=dict(l=0, r=40, t=10, b=0),
            coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
            **dc.TRANSPARENT,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Generic terms Naukri tags as skills — 'Agile', 'Coding', 'Cloud' — are "
            "excluded via a blocklist."
        )


with tab2:
    st.write("Which skill pairs show up together most often, ranked strongest first.")
    top_n = st.slider("Pairings to show", 5, 30, 15, key="pairs_top_n")

    # Candidate pool is fixed and deliberately wider than what's shown —
    # ranking needs enough pairs to choose from, but the reader only
    # picks how many results they see, not how many are considered.
    pairs = dc.co_occurrence(top_n=30)

    if pairs.empty:
        st.info("Not enough data for pairings yet.")
    else:
        top_pairs = pairs.nlargest(top_n, "together").copy()
        top_pairs["pair"] = top_pairs["skill_a"] + " + " + top_pairs["skill_b"]

        fig = px.bar(
            top_pairs.sort_values("together"), x="together", y="pair", orientation="h",
            text="together", color="together", color_continuous_scale=dc.SCALE,
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(
            height=max(340, top_n * 28), margin=dict(l=0, r=40, t=10, b=0),
            coloraxis_showscale=False, xaxis_title="postings asking for both", yaxis_title=None,
            **dc.TRANSPARENT,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Ranked among the 30 most in-demand skills — a pair outside that pool wouldn't appear here even if it were common.")

        st.divider()
        st.write("Full picture: every pairing among those same skills, not just the top ones.")

        mirrored = pairs.rename(columns={"skill_a": "skill_b", "skill_b": "skill_a"})
        both = pd.concat([pairs, mirrored], ignore_index=True)
        matrix = both.pivot_table(index="skill_a", columns="skill_b", values="together", fill_value=0)

        heatmap = px.imshow(matrix, color_continuous_scale=dc.SCALE, aspect="auto",
                            labels=dict(color="postings together"))
        # Same box-separation treatment as the experience-level chart —
        # a gap between cells so each pairing reads as its own square
        # instead of bleeding into its neighbors.
        heatmap.update_traces(xgap=2, ygap=2)
        heatmap.update_layout(height=620, margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(heatmap, use_container_width=True)
        st.caption("Darker cells mean the two skills are more often requested together. The diagonal (a skill against itself) is always empty.")


with tab5:
    # The counterpart to "Pairings": that tab shows skills wanted
    # TOGETHER, this one shows skills accepted INSTEAD of each other.
    st.write("Skills employers treat as swappable — a posting asking for one of these "
             "would take any of the others.")

    choices = dc.skill_choices(limit=15, min_postings=2)
    flex = dc.skill_flexibility(limit=15, min_postings=5)

    if choices.empty:
        st.info("No interchangeable sets detected yet.")
    else:
        choices["set"] = choices["skills"].apply(lambda s: " / ".join(s))
        fig = px.bar(choices.sort_values("postings"), x="postings", y="set",
                     orientation="h", text="postings",
                     color="postings", color_continuous_scale=dc.SCALE)
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=max(340, len(choices) * 30),
                          margin=dict(l=0, r=40, t=10, b=0), coloraxis_showscale=False,
                          xaxis_title="postings offering this choice", yaxis_title=None,
                          **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Read as 'any one of these will do'. Detected from the wording of each "
                   "description, so treat it as a strong hint rather than a guarantee — "
                   "roughly one set in four is wrong.")

    if not flex.empty:
        st.divider()
        st.subheader("How negotiable is each skill?")
        st.write("Of the postings wanting a skill, the share that would equally accept "
                 "something else. High means employers care about the capability more "
                 "than the specific tool.")

        flex["swaps_label"] = flex["swaps"].apply(lambda s: ", ".join(s[:3]) if len(s) else "-")
        fig2 = px.bar(flex.sort_values("negotiable_pct"), x="negotiable_pct", y="skill",
                      orientation="h", text="negotiable_pct",
                      color="negotiable_pct", color_continuous_scale=dc.SCALE,
                      hover_data={"required": True, "alternative": True, "swaps_label": True})
        fig2.update_traces(texttemplate="%{text:.0f}%", textposition="outside", cliponaxis=False)
        fig2.update_layout(height=max(340, len(flex) * 30),
                           margin=dict(l=0, r=50, t=10, b=0), coloraxis_showscale=False,
                           xaxis_title="% of demand that would accept a substitute",
                           yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(fig2, use_container_width=True)

        st.dataframe(
            flex[["skill", "required", "alternative", "negotiable_pct", "swaps_label"]]
                .rename(columns={"required": "asked for outright",
                                 "alternative": "would accept a swap",
                                 "negotiable_pct": "negotiable %",
                                 "swaps_label": "usually swapped with"}),
            use_container_width=True, hide_index=True)
        st.caption("Only skills that appear in at least one choice set can score above zero, "
                   "so a skill missing here was never offered as an alternative to anything.")


with tab3:
    st.write("Whether junior and senior postings ask for different things.")

    bands = [("Junior (0-3)", 0, 3), ("Mid (4-7)", 4, 7), ("Senior (8+)", 8, None)]
    frames = []
    for label, lo, hi in bands:
        d = dc.skill_demand(limit=18, experience_min=lo, experience_max=hi)
        if not d.empty:
            d["level"] = label
            frames.append(d)

    if not frames:
        st.info("No data yet.")
    else:
        by_level = pd.concat(frames, ignore_index=True)
        totals = by_level.groupby("level")["postings"].transform("sum")
        by_level["share"] = 100 * by_level["postings"] / totals

        matrix = by_level.pivot_table(index="skill", columns="level", values="share", fill_value=0)

        # Sorted by how much a skill's share swings between levels — the
        # biggest differences are the actual point of this chart, so they
        # belong at the top instead of being buried in alphabetical order.
        swing = (matrix.max(axis=1) - matrix.min(axis=1)).sort_values(ascending=False)
        matrix = matrix.reindex(swing.index)

        fig = px.imshow(matrix, color_continuous_scale=dc.SCALE, aspect="auto",
                        labels=dict(color="% of that level's skill mentions"))
        # A gap between cells turns the grid into distinct boxes instead
        # of one continuous smear of color — makes each skill/level cell
        # readable on its own rather than bleeding into its neighbors.
        fig.update_traces(xgap=3, ygap=3)
        fig.update_layout(height=560, margin=dict(l=0, r=0, t=10, b=0),
                          xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Sorted top-to-bottom by how much each skill's share shifts across "
            "levels, so the biggest junior-vs-senior differences stand out first. "
            "Shown as a share within each level rather than raw counts, otherwise "
            "the level with the most postings would dominate every row."
        )

with tab4:
    st.write("What share of a role's skill mentions fall into each technical category, and how that mix shifts from one role to another.")

    role_options = ["All roles"] + dc.roles()
    role_choice = st.selectbox("Role", role_options, key="cat_role")
    filters = {} if role_choice == "All roles" else {"role_family": [role_choice]}

    mix = dc.skill_category_mix(**filters)

    if mix.empty:
        st.info("No categorized skill data yet for this role.")
    else:
        fig = px.bar(
            mix.sort_values("postings"), x="postings", y="bucket", orientation="h",
            text="postings", color="postings", color_continuous_scale=dc.SCALE,
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(
            height=280, margin=dict(l=0, r=40, t=10, b=0),
            coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
            **dc.TRANSPARENT,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Counts skill mentions, not postings — a posting with three Cloud/DevOps "
            "skills contributes three, so shares reflect a real mix rather than an "
            "overlap count. Only categorized skills are included; most of Naukri's own "
            "tags ('Agile', 'Communication Skills', generic role titles like "
            "'Full Stack Developer') aren't a specific enough technology to categorize, "
            "so they're excluded rather than forced into a catch-all bucket."
        )

dc.sampling_note()
