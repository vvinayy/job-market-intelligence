"""Jobs — search individual postings.

Every other page shows aggregates; this is the one place to see (and
filter down to) the actual listings behind them. Thin wrapper over the
/postings endpoint, which already does the filtering, sorting and
pagination server-side.
"""

import pandas as pd
import streamlit as st

import dash_common as dc

st.set_page_config(page_title="Jobs", layout="wide", page_icon="◎")
st.title("Jobs")
st.caption("Search individual postings. Pick what you already know — the sidebar pages show the aggregate picture.")


# =====================================================================
# FILTERS
# =====================================================================
f1, f2, f3 = st.columns(3)
skill = f1.multiselect("Skills (any of)", dc.top_skills(80), key="jobs_skill")
role_family = f2.multiselect("Role", dc.roles(), key="jobs_role")
cities_df = dc.cities_reference()
city = f3.multiselect("City", cities_df["city_name"].tolist() if not cities_df.empty else [], key="jobs_city")

f4, f5, f6 = st.columns(3)
exp = f4.slider("Experience range required (years)", 0, 20, (0, 20), key="jobs_exp",
                 help="Matches postings whose minimum requirement falls in this range.")
working_type = f5.multiselect("Work arrangement", ["On-site", "Hybrid", "Remote"], key="jobs_wt")
has_salary = f6.checkbox("Only postings that disclose salary", key="jobs_salary")

f7, f8, f9 = st.columns(3)
search = f7.text_input("Search title or company", key="jobs_search")
sort_by = f8.selectbox(
    "Sort by",
    ["posted_date", "experience_min", "salary_max", "openings", "times_seen", "company", "title"],
    key="jobs_sort",
)
order = f9.radio("Order", ["desc", "asc"], horizontal=True, key="jobs_order")

page_size = st.select_slider("Results per page", [10, 25, 50, 100], value=25, key="jobs_page_size")

st.divider()


# =====================================================================
# PAGINATION STATE — reset to page 1 whenever a filter actually changes,
# otherwise changing a filter on page 3 would silently keep querying
# page 3 of the NEW result set.
# =====================================================================
filters = dict(
    skill=skill or None, role_family=role_family or None, city=city or None,
    experience_min=exp[0], experience_max=exp[1],
    working_type=working_type or None, has_salary=True if has_salary else None,
    search=search or None,
)
signature = (tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in filters.items())),
             sort_by, order, page_size)

if st.session_state.get("jobs_signature") != signature:
    st.session_state["jobs_page"] = 1
    st.session_state["jobs_signature"] = signature

page = st.session_state.get("jobs_page", 1)


# =====================================================================
# RESULTS
# =====================================================================
items, meta = dc.search_postings(page=page, page_size=page_size, sort_by=sort_by,
                                  order=order, **filters)
total = meta.get("total") or 0

if total == 0:
    st.info("No postings match these filters.")
    st.stop()

pages = meta.get("pages") or 1
nav1, nav2, nav3 = st.columns([1, 3, 1])
if nav1.button("← Previous", disabled=page <= 1):
    st.session_state["jobs_page"] = page - 1
    st.rerun()
nav2.markdown(f"<div style='text-align:center'>Page {page} of {pages} — {total} postings</div>",
              unsafe_allow_html=True)
if nav3.button("Next →", disabled=page >= pages):
    st.session_state["jobs_page"] = page + 1
    st.rerun()

display = items.copy()
display["experience"] = display.apply(
    lambda r: f"{r.experience_min:g}-{r.experience_max:g} yrs" if pd.notna(r.experience_min) else "Not stated",
    axis=1)
display["salary"] = display.apply(
    lambda r: f"{r.salary_min:g}-{r.salary_max:g} LPA" if pd.notna(r.salary_min) else "Not disclosed",
    axis=1)
display["cities"] = display["cities"].apply(lambda c: ", ".join(c) if c else "Not stated")
display["skills"] = display["skills"].apply(lambda s: ", ".join(s[:6]) + (f" +{len(s)-6} more" if len(s) > 6 else ""))

cols = ["title", "company", "role_family", "experience", "cities", "working_type",
        "salary", "skills", "posted_date", "url"]

event = st.dataframe(
    display[cols],
    use_container_width=True,
    hide_index=True,
    height=min(600, 60 + 36 * len(display)),
    column_config={
        "url": st.column_config.LinkColumn("Listing", display_text="Open ↗"),
        "role_family": "Role",
        "working_type": "Arrangement",
        "posted_date": "Posted",
    },
    on_select="rerun",
    selection_mode="single-row",
)

st.caption("Click a row to see the full description.")


# =====================================================================
# DETAIL — only fetched for the row actually selected, since the
# description is heavy and most rows are never opened.
# =====================================================================
selected_rows = event.selection.rows if event and event.selection else []
if selected_rows:
    job_id = int(items.iloc[selected_rows[0]]["job_id"])
    detail = dc.posting_detail(job_id)

    with st.container(border=True):
        st.subheader(f"{detail.get('title') or 'Untitled'} — {detail.get('company') or 'Unknown company'}")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Experience", display.iloc[selected_rows[0]]["experience"])
        d2.metric("Salary", display.iloc[selected_rows[0]]["salary"])
        d3.metric("Times seen", detail.get("times_seen") or 1)
        d4.metric("Days listed", detail.get("days_listed") if detail.get("days_listed") is not None else "—")

        if detail.get("key_skills"):
            st.markdown("**Key skills:** " + ", ".join(detail["key_skills"]))
        if detail.get("tech_in_description"):
            st.markdown("**Also mentioned in the description:** " + ", ".join(detail["tech_in_description"]))

        st.markdown("**Description**")
        st.write(detail.get("description") or "No description available.")

        if detail.get("url"):
            st.link_button("Open on Naukri", detail["url"])

dc.sampling_note()
