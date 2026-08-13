"""Skills — demand, pairings, seniority split, and the network view."""

import math
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

import dash_common as dc

st.set_page_config(page_title="Skills", layout="wide", page_icon="◎")
st.title("Skills")

tab1, tab2, tab3, tab4 = st.tabs(["Demand", "Pairings", "By experience level", "Network"])


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
    st.write("How often two skills are asked for in the same posting.")
    n = st.slider("Skills to compare", 8, 30, 16, key="matrix_n")

    pairs = dc.co_occurrence(top_n=n)
    if pairs.empty:
        st.info("Not enough data for pairings yet.")
    else:
        import pandas as pd
        mirrored = pairs.rename(columns={"skill_a": "skill_b", "skill_b": "skill_a"})
        both = pd.concat([pairs, mirrored], ignore_index=True)
        matrix = both.pivot_table(index="skill_a", columns="skill_b", values="together", fill_value=0)

        fig = px.imshow(matrix, color_continuous_scale=dc.SCALE, aspect="auto",
                        labels=dict(color="postings together"))
        fig.update_layout(height=620, margin=dict(l=0, r=0, t=10, b=0),
                          xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Darker cells mean the two skills are more often requested together.")


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
        import pandas as pd
        by_level = pd.concat(frames, ignore_index=True)
        totals = by_level.groupby("level")["postings"].transform("sum")
        by_level["share"] = 100 * by_level["postings"] / totals

        matrix = by_level.pivot_table(index="skill", columns="level", values="share", fill_value=0)
        fig = px.imshow(matrix, color_continuous_scale=dc.SCALE, aspect="auto",
                        labels=dict(color="% of that level's skill mentions"))
        fig.update_layout(height=560, margin=dict(l=0, r=0, t=10, b=0),
                          xaxis_title=None, yaxis_title=None, **dc.TRANSPARENT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Shown as a share within each level rather than raw counts, otherwise "
            "the level with the most postings would dominate every row."
        )


with tab4:
    st.write(
        "Skills arranged on a ring, sized by demand, connected where employers "
        "ask for them together. A dominant skill like Python connects to almost "
        "everything, which crowds the picture — the **Pairings** tab is more "
        "precise for exact comparisons."
    )

    cA, cB = st.columns(2)
    n_nodes = cA.slider("Skills in the network", 8, 30, 16, key="net_n")
    n_edges = cB.slider("Strongest connections to draw", 10, 120, 40, key="net_e")

    counts = dc.skill_demand(limit=n_nodes)
    pairs = dc.co_occurrence(top_n=n_nodes)
    pairs = pairs.nlargest(n_edges, "together") if not pairs.empty else pairs

    if counts.empty:
        st.info("No skill data yet.")
    else:
        skills = counts["skill"].tolist()
        angles = {s: 2 * math.pi * i / len(skills) for i, s in enumerate(skills)}
        pos = {s: (math.cos(a), math.sin(a)) for s, a in angles.items()}
        demand = dict(zip(counts["skill"], counts["postings"]))

        fig = go.Figure()

        if not pairs.empty:
            widest, weakest = pairs["together"].max(), pairs["together"].min()
            span = max(widest - weakest, 1)
            for _, row in pairs.iterrows():
                if row.skill_a not in pos or row.skill_b not in pos:
                    continue
                x0, y0 = pos[row.skill_a]
                x1, y1 = pos[row.skill_b]
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                strength = (row.together - weakest) / span
                fig.add_trace(go.Scatter(
                    x=[x0, mx * 0.62, x1], y=[y0, my * 0.62, y1], mode="lines",
                    line=dict(width=0.5 + 4.0 * strength,
                              color=f"rgba(0,180,200,{0.12 + 0.42 * strength:.2f})", shape="spline"),
                    hoverinfo="text",
                    hovertext=f"{row.skill_a} + {row.skill_b}<br>{row.together} postings together",
                    showlegend=False,
                ))

        biggest = max(demand.values())
        fig.add_trace(go.Scatter(
            x=[pos[s][0] for s in skills], y=[pos[s][1] for s in skills],
            mode="markers+text",
            marker=dict(
                size=[16 + 34 * demand[s] / biggest for s in skills],
                color=[demand[s] for s in skills],
                colorscale=[[0, "#6b7280"], [0.5, "#00b4c8"], [1, "#c8d400"]],
                line=dict(width=1.5, color="rgba(255,255,255,0.35)"), showscale=False,
            ),
            text=skills, textposition="top center", textfont=dict(size=11),
            hovertext=[f"<b>{s}</b><br>{demand[s]} postings" for s in skills],
            hoverinfo="text", showlegend=False,
        ))
        fig.update_layout(
            height=620, margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(visible=False, range=[-1.35, 1.35]),
            yaxis=dict(visible=False, range=[-1.3, 1.3], scaleanchor="x"),
            hovermode="closest", **dc.TRANSPARENT,
        )
        st.plotly_chart(fig, use_container_width=True)

        if not pairs.empty:
            st.caption(f"Showing the {len(pairs)} strongest pairings.")

dc.sampling_note()
