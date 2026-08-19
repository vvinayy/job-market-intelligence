"""
Job Market Intelligence — landing page.

Run with:
    uvicorn api.main:app --reload      (separate terminal)
    streamlit run Home.py

Reads exclusively through dash_common's API wrappers — this file has no
knowledge of table or column names, only of what the API returns.
"""

import time
import math
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import dash_common as dc


st.set_page_config(page_title="Job Market Intelligence", layout="wide", page_icon="◎")

st.title("Job Market Intelligence")
st.caption("What Indian IT employers are actually asking for")


facts = dc.summary()
exp = dc.experience_distribution()

total = int(facts.get("total_postings") or 0)

if total == 0:
    st.warning("No postings collected yet. Run the scraper first.")
    st.stop()

fresher_row = exp[exp["bucket"] == "0-1 years"] if not exp.empty else exp
fresher = int(fresher_row["postings"].iloc[0]) if not fresher_row.empty else 0


# =====================================================================
# ANIMATED COUNTERS — once per session, not on every rerun.
# =====================================================================
def count_up(container, target: int, label: str, suffix: str = "", steps: int = 22):
    if st.session_state.get("counted"):
        container.metric(label, f"{target:,}{suffix}")
        return
    for i in range(steps + 1):
        value = int(target * (i / steps) ** 0.6)
        container.metric(label, f"{value:,}{suffix}")
        time.sleep(0.012)
    container.metric(label, f"{target:,}{suffix}")


a1, a2, a3 = st.columns(3)
count_up(a1.empty(), total, "Postings analysed")
count_up(a2.empty(), int(facts.get("companies") or 0), "Employers")
count_up(a3.empty(), round(facts.get("avg_skills_per_posting") or 0), "Skills asked per role", suffix=" avg")
st.session_state["counted"] = True

st.divider()


# =====================================================================
# FINDINGS AS SENTENCES
# =====================================================================
st.subheader("What the data says")

def one_in(count: int, of: int) -> str:
    return "None" if count == 0 else f"1 in {round(of / count)}"

s1, s2, s3 = st.columns(3)

with s1:
    st.markdown(f"### {one_in(fresher, total)}")
    st.markdown("**postings accept freshers**")
    st.caption(f"{fresher} of {total} allow 0–1 years of experience.")

with s2:
    active = int(facts.get("active_last_7_days") or 0)
    st.markdown(f"### {active} of {total}")
    st.markdown("**postings still active this week**")
    st.caption("Listed again in the last 7 days — the rest haven't reappeared since.")

with s3:
    st.markdown(f"### {facts.get('avg_skills_per_posting')}")
    st.markdown("**skills wanted per role**")
    st.caption("Averaged across every posting collected.")

st.divider()


# =====================================================================
# WAFFLE CHART — 100 squares, one per posting-in-a-hundred.
# =====================================================================
st.subheader("If 100 postings were on the table")

order = ['0-1 years', '2-3 years', '4-6 years', '7-10 years', '10+ years', 'Not stated']
colors = {
    '0-1 years': '#c8d400', '2-3 years': '#00b4c8', '4-6 years': '#0891a5',
    '7-10 years': '#4b5563', '10+ years': '#374151', 'Not stated': '#1f2937',
}

lookup = dict(zip(exp["bucket"], exp["postings"])) if not exp.empty else {}
present = [b for b in order if b in lookup]

if present:
    exact = {b: 100 * lookup[b] / total for b in present}
    squares = {b: int(v) for b, v in exact.items()}
    for b in sorted(present, key=lambda b: exact[b] - squares[b], reverse=True):
        if sum(squares.values()) >= 100:
            break
        squares[b] += 1

    labels = []
    for b in present:
        labels.extend([b] * squares[b])
    labels = labels[:100]

    xs, ys, cs, hover = [], [], [], []
    for i, band in enumerate(labels):
        xs.append(i % 20)
        ys.append(-(i // 20))
        cs.append(colors.get(band, '#1f2937'))
        hover.append(f"{band}<br>{squares[band]} in 100 postings")

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="markers",
        marker=dict(size=20, color=cs, symbol="square"),
        hovertext=hover, hoverinfo="text",
    ))
    fig.update_layout(
        height=200, margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False, range=[-0.6, 19.6]),
        yaxis=dict(visible=False, range=[-4.6, 0.6], scaleanchor="x"),
        showlegend=False, **dc.TRANSPARENT,
    )
    st.plotly_chart(fig, use_container_width=True)

    legend = "  ".join(f"<span style='color:{colors[b]}'>■</span> {b} ({squares[b]})" for b in present)
    st.markdown(f"<div style='font-size:0.9em'>{legend}</div>", unsafe_allow_html=True)
    st.caption("Each square is one posting in a hundred. Hover for exact figures.")

st.divider()


# =====================================================================
# TOP SKILLS
# =====================================================================
st.subheader("What employers are asking for")

n = st.slider("Skills to show", 5, 30, 15, key="home_skills")
skills = dc.skill_demand(limit=n)

if not skills.empty:
    fig = px.bar(
        skills.sort_values("postings"), x="postings", y="skill", orientation="h",
        text="postings", color="postings", color_continuous_scale=dc.SCALE,
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(300, n * 26), margin=dict(l=0, r=40, t=10, b=0),
        coloraxis_showscale=False, xaxis_title=None, yaxis_title=None,
        **dc.TRANSPARENT,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Generic tags like 'Agile' and 'Coding' are filtered out. See **Skills** for pairings and seniority splits.")

st.divider()


# =====================================================================
# WHAT TO LEARN NEXT
# =====================================================================
st.subheader("What should I learn next?")
st.write("Pick what you already know. This finds what employers ask for alongside it.")

known = st.multiselect("Skills you have", dc.top_skills(60), key="known")

if known:
    suggestions, base = dc.skill_suggestions(known, limit=12)
    if base == 0 or suggestions.empty:
        st.info("No postings in the data ask for those skills yet.")
    else:
        st.caption(f"Based on {base} postings asking for at least one of your skills.")
        fig = go.Figure(go.Bar(
            x=suggestions["share_pct"], y=suggestions["skill"], orientation="h",
            marker=dict(color=suggestions["share_pct"], colorscale=[[0, "#6b7280"], [1, "#c8d400"]]),
            text=[f"{p:.0f}%" for p in suggestions["share_pct"]], textposition="outside",
            hovertext=[f"{r.skill}<br>{r.postings} of {base} postings" for r in suggestions.itertuples()],
            hoverinfo="text",
        ))
        fig.update_layout(
            height=max(280, len(suggestions) * 30),
            margin=dict(l=0, r=40, t=10, b=0), yaxis=dict(autorange="reversed"),
            xaxis_title="share of matching postings", yaxis_title=None, **dc.TRANSPARENT,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "These are skills often requested together — not evidence that learning "
            "them causes hiring."
        )

st.divider()

with st.expander("System health"):
    health = dc.scrape_health()
    latest = health.get("latest_run")

    if not latest:
        st.info("No scrape runs logged yet.")
    else:
        for w in health.get("warnings") or []:
            st.warning(
                f"**{w['field']}** dropped to {w['current_rate']:.0%} of postings this run "
                f"(usually {w['historical_avg_rate']:.0%}) — a selector may be broken."
            )
        if not latest.get("storage_ok"):
            st.error(f"Last run's database write failed: {latest.get('error_message')}")
        if not health.get("warnings") and latest.get("storage_ok"):
            st.success("No issues detected in the most recent run.")

        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Last run", latest["started_at"][:16].replace("T", " "))
        h2.metric("Postings written", latest.get("postings_written") or 0)
        duration = latest.get("duration_seconds")
        h3.metric("Duration", f"{duration:.0f}s" if duration is not None else "—")
        h4.metric("Storage OK", "Yes" if latest.get("storage_ok") else "No")

        runs = health.get("recent_runs") or []
        if len(runs) > 1:
            recent_df = pd.DataFrame(runs)[["started_at", "search_url", "postings_written", "duration_seconds", "storage_ok"]]
            st.dataframe(recent_df, use_container_width=True, hide_index=True)

st.write("**More:** the sidebar has skill pairings, market composition, and trends.")
dc.sampling_note()
