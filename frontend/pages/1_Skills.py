"""Skills — demand, one skill in depth, pairings, interchangeable sets,
experience split, category mix."""

from urllib.parse import quote_plus

import pandas as pd
import streamlit as st
import plotly.express as px

import dash_common as dc

st.set_page_config(page_title="Skills", layout="wide", page_icon="◎")
st.title("Skills")
dc.freshness_note()

tab1, tab_one, tab2, tab5, tab3, tab4 = st.tabs(
    ["Demand", "One skill", "Pairings", "Interchangeable", "By experience level", "By category"])

# Below this a skill's role and employer breakdowns are one or two postings
# each -- a chart of noise. 136 skills cleared it on 2026-09-30.
ONE_SKILL_MIN_POSTINGS = 20
EXPERIENCE_ORDER = ["0-1 years", "2-3 years", "4-6 years", "7-10 years", "10+ years", "Not stated"]


def _hbar(frame: pd.DataFrame, x: str, y: str, xaxis_title: str | None = None, text=None):
    fig = px.bar(frame, x=x, y=y, orientation="h", text=text if text is not None else x,
                 color=x, color_continuous_scale=dc.SCALE)
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(height=max(260, len(frame) * 30), margin=dict(l=0, r=50, t=10, b=0),
                      coloraxis_showscale=False, xaxis_title=xaxis_title, yaxis_title=None,
                      **dc.TRANSPARENT)
    st.plotly_chart(fig, use_container_width=True)


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
            "How many postings ask for each skill. Naukri tags some very broad "
            "terms as skills — 'Agile', 'Coding', 'Cloud' — and those are left out "
            "so they don't crowd out real tools."
        )
        dc.csv_download(data, f"skill_demand_top{n}.csv")


with tab_one:
    st.write("Everything about a single skill in one place: who asks for it, at what "
             "level, what comes with it, and how demand has moved.")

    ranked = dc.skill_demand(limit=200)
    options = (ranked.loc[ranked["postings"] >= ONE_SKILL_MIN_POSTINGS, "skill"].tolist()
               if not ranked.empty else [])

    if not options:
        st.info("No skill has enough postings yet.")
    else:
        # ?skill=Kubernetes opens this tab's picker on that skill.
        dc.from_url("one_skill", "skill", options=options)
        c1, c2 = st.columns([3, 2])
        chosen = c1.selectbox(
            "Skill", options, key="one_skill",
            help=f"Skills asked for in at least {ONE_SKILL_MIN_POSTINGS} postings, most "
                 "in-demand first. Below that the breakdowns are one or two postings each.")
        counted = c2.radio(
            "Count", ["All postings", "Open only"], horizontal=True, key="one_skill_scope",
            help="Open only leaves out postings Naukri has confirmed closed. hirist "
                 "never says when a posting closes, so its postings always count as open.")
        open_only = counted == "Open only"
        dc.to_url(skill=chosen)

        profile = dc.skill_profile(chosen, open_only=open_only)
        n = int(profile.get("postings") or 0)
        which = "open postings" if open_only else "postings"

        m1, m2, m3 = st.columns(3)
        m1.metric(f"{which.capitalize()} asking for it", f"{n:,}")
        m2.metric(f"Share of all {which}", f"{profile.get('share_pct', 0):.0f}%")
        m3.metric("Either/or only", int(profile.get("alternative_only") or 0),
                  help=f"Postings that accept {chosen} as one of several choices "
                       f"(e.g. '{chosen} or similar') rather than as a must-have. "
                       "They are included in the count, as everywhere else on this dashboard.")

        left, right = st.columns(2)
        with left:
            st.markdown("**Which roles ask for it**")
            roles_for = dc.role_distribution(skill=chosen, open_only=open_only)
            if not roles_for.empty:
                _hbar(roles_for.head(8).sort_values("postings"), "postings", "role")
        with right:
            st.markdown("**At what experience level**")
            exp_for = dc.experience_distribution(skill=chosen, open_only=open_only)
            if not exp_for.empty:
                exp_for["order"] = exp_for["bucket"].map(EXPERIENCE_ORDER.index)
                _hbar(exp_for.sort_values("order", ascending=False), "postings", "bucket")

        left, right = st.columns(2)
        with left:
            st.markdown("**Usually asked for together**")
            paired = pd.DataFrame(profile.get("paired_with") or [])
            if not paired.empty:
                _hbar(paired.sort_values("share_pct"), "share_pct", "skill",
                      xaxis_title=f"% of {chosen} postings",
                      text=paired.sort_values("share_pct")["share_pct"].map(lambda v: f"{v:.0f}%"))
        with right:
            st.markdown("**Who asks for it most**")
            employers = pd.DataFrame(profile.get("top_employers") or [])
            if not employers.empty:
                _hbar(employers.sort_values("postings"), "postings", "name")

        st.markdown("**Demand over time**")
        series = dc.skill_series([chosen])
        if not series.empty:
            fig = px.line(series, x="snapshot_date", y="posting_count", markers=True,
                          color_discrete_sequence=[dc.PALETTE[0]])
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title=None, yaxis_title="postings", **dc.TRANSPARENT)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Postings asking for it that the daily searches found on each "
                       "day — the same series as the Trends page, whatever the Count "
                       "setting above. A step can come from what we collect rather "
                       "than the market: the hirist searches added on 2–4 Sep lifted "
                       "most skills at once.")

        st.link_button(f"See {chosen} postings in Jobs", f"/Jobs?skill={quote_plus(chosen)}")


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
        st.caption("Skills most often asked for in the same posting. Only the 30 "
                   "most in-demand skills are paired up, so a common pairing "
                   "between two rarer skills won't show here.")
        dc.csv_download(pairs.sort_values("together", ascending=False),
                        "skill_pairings.csv", "Download all pairings (CSV)")

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
            "What kind of work each role is really made of, as a share of the skills "
            "it names. A role wanting three cloud tools counts three times over, so "
            "the shares read as a mix rather than a headcount. Only skills specific "
            "enough to place in a category are shown — broad tags like 'Agile' or "
            "'Communication Skills' are left out rather than lumped into 'Other'."
        )

dc.sampling_note()
