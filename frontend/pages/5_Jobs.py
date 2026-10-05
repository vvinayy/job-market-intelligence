"""Jobs — search individual postings.

Every other page shows aggregates; this is the one place to see (and
filter down to) the actual listings behind them. Thin wrapper over the
/postings endpoint, which already does the filtering, sorting and
pagination server-side.
"""

from datetime import date

import pandas as pd
import streamlit as st

import dash_common as dc

st.set_page_config(page_title="Jobs", layout="wide", page_icon="◎")
st.title("Jobs")
st.caption("Search individual postings. Pick what you already know — the sidebar pages show the aggregate picture.")
dc.freshness_note()


# =====================================================================
# FILTERS
# =====================================================================
# Every filter is seeded from the page URL (once) and written back to it below,
# so a search can be bookmarked or shared and reopens exactly as it was.
# 200, not 80: the Skills page's "One skill" tab links here for any skill in at
# least 20 postings (136 of them), and a skill missing from this list would be
# dropped from the link.
SKILL_OPTIONS = dc.top_skills(200)
ROLE_OPTIONS = dc.roles()
cities_df = dc.cities_reference()
CITY_OPTIONS = cities_df["city_name"].tolist() if not cities_df.empty else []
sources_df = dc.sources()
source_counts = (dict(zip(sources_df["name"], sources_df["postings"]))
                 if not sources_df.empty else {})
STATUS_OPTIONS = ["All", "Still open", "Closed"]
SENIORITY_OPTIONS = ["Intern/Trainee", "Junior", "Associate", "Senior", "Lead/Principal", "Manager/Leadership"]
WT_OPTIONS = ["On-site", "Hybrid", "Remote"]
QUAL_OPTIONS = ["UG", "PG", "Doctorate"]
SORT_OPTIONS = ["posted_date", "expired_on", "experience_min", "salary_max", "openings",
                "applicant_count", "times_seen", "company", "title"]
ORDER_OPTIONS = ["desc", "asc"]
PAGE_SIZES = [10, 25, 50, 100]


def _exp_range(text: str) -> tuple[int, int]:
    lo, hi = (int(x) for x in text.split("-"))
    if not 0 <= lo <= hi <= 20:
        raise ValueError(text)
    return lo, hi


dc.from_url("jobs_skill", "skill", many=True, options=SKILL_OPTIONS)
dc.from_url("jobs_role", "role", many=True, options=ROLE_OPTIONS)
dc.from_url("jobs_city", "city", many=True, options=CITY_OPTIONS)
dc.from_url("jobs_source", "board", many=True, options=list(source_counts))
dc.from_url("jobs_status", "status", options=STATUS_OPTIONS)
dc.from_url("jobs_seniority", "seniority", many=True, options=SENIORITY_OPTIONS)
dc.from_url("jobs_exp", "exp", parse=_exp_range)
dc.from_url("jobs_wt", "arrangement", many=True, options=WT_OPTIONS)
dc.from_url("jobs_salary", "salary", parse=lambda s: s == "1")
dc.from_url("jobs_qual", "education", many=True, options=QUAL_OPTIONS)
dc.from_url("jobs_search", "q")
dc.from_url("jobs_sort", "sort", options=SORT_OPTIONS)
dc.from_url("jobs_order", "order", options=ORDER_OPTIONS)
dc.from_url("jobs_page_size", "per_page", parse=int, options=PAGE_SIZES)
# Defaults go through session state rather than the widgets' value= so a
# URL-seeded value never collides with a hardcoded default.
st.session_state.setdefault("jobs_exp", (0, 20))
st.session_state.setdefault("jobs_page_size", 25)

f1, f2, f3 = st.columns(3)
skill = f1.multiselect("Skills (any of)", SKILL_OPTIONS, key="jobs_skill")
role_family = f2.multiselect("Role", ROLE_OPTIONS, key="jobs_role")
city = f3.multiselect("City", CITY_OPTIONS, key="jobs_city")

source = st.multiselect(
    "Job board", list(source_counts), key="jobs_source",
    format_func=lambda s: f"{s} ({source_counts.get(s, 0)})",
    help="Where the posting was collected from. Leave empty for all boards.",
)
if source_counts:
    st.caption(
        "Counts are deliberately on the control: "
        + ", ".join(f"**{n}** {c}" for n, c in source_counts.items())
        + ". The boards are not equal shares of this data, and they do not "
          "publish the same fields — a blank column can mean the board never "
          "states it rather than the employer not saying."
    )

status = st.radio(
    "Listing status", STATUS_OPTIONS, horizontal=True, key="jobs_status",
    help="Checked daily against Naukri only. 'Closed' means Naukri now "
         "redirects the posting as expired — it does not mean the role was "
         "filled. hirist never says when a posting closes, so its postings "
         "show 'Listed' with the date our searches last saw them: they appear "
         "under Still open once seen more than once, never under Closed.",
)
is_expired = {"All": None, "Still open": False, "Closed": True}[status]

# hirist rows are never checked. A scrape writes is_expired = FALSE on every
# re-sighting, so a hirist posting seen twice matches Still open (shown as
# "Listed", not "Open"); one seen only once is NULL and matches neither.
if is_expired is not None and any(s != "naukri" for s in source):
    st.info(
        "**Only Naukri postings are checked for closure.** hirist postings "
        "appear under *Still open* only once our searches have seen them more "
        "than once, labelled *Listed* with that date — not confirmed open — "
        "and never under *Closed*. Set status to *All* to see every one."
    )

seniority_level = st.multiselect(
    "Seniority (inferred from title)", SENIORITY_OPTIONS, key="jobs_seniority",
    help="Most titles carry no seniority word at all — leaving this empty includes those too.",
)

f4, f5, f6, f10 = st.columns(4)
exp = f4.slider("Experience range required (years)", 0, 20, key="jobs_exp",
                 help="Matches postings whose minimum requirement falls in this range.")
working_type = f5.multiselect("Work arrangement", WT_OPTIONS, key="jobs_wt")
has_salary = f6.checkbox("Only postings that disclose salary", key="jobs_salary")
qualification_level = f10.multiselect("Education level", QUAL_OPTIONS, key="jobs_qual",
                                       help="Only postings scraped since Education tracking was added carry this field.")

f7, f8, f9 = st.columns(3)
search = f7.text_input("Search title or company", key="jobs_search")
sort_by = f8.selectbox("Sort by", SORT_OPTIONS, key="jobs_sort")
order = f9.radio("Order", ORDER_OPTIONS, horizontal=True, key="jobs_order")

page_size = st.select_slider("Results per page", PAGE_SIZES, key="jobs_page_size")

dc.to_url(
    skill=skill, role=role_family, city=city, board=source,
    status=None if status == "All" else status, seniority=seniority_level,
    exp=None if tuple(exp) == (0, 20) else f"{exp[0]}-{exp[1]}",
    arrangement=working_type, salary="1" if has_salary else None,
    education=qualification_level, q=search.strip() or None,
    sort=None if sort_by == "posted_date" else sort_by,
    order=None if order == "desc" else order,
    per_page=None if page_size == 25 else page_size,
)

st.divider()


# =====================================================================
# PAGINATION STATE — reset to page 1 whenever a filter actually changes,
# otherwise changing a filter on page 3 would silently keep querying
# page 3 of the NEW result set.
# =====================================================================
filters = dict(
    skill=skill or None, role_family=role_family or None, seniority_level=seniority_level or None,
    city=city or None,
    experience_min=exp[0], experience_max=exp[1],
    working_type=working_type or None, has_salary=True if has_salary else None,
    qualification_level=qualification_level or None,
    search=search or None,
    is_expired=is_expired,
    source=source or None,
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
def _status(r) -> str:
    """'Open' only when the checker verified it. A posting nothing has checked
    -- every hirist one, and a Naukri one before its first check -- says what we
    do know: when our searches last saw it."""
    if pd.notna(r.is_expired) and bool(r.is_expired):
        return "Closed"
    if pd.notna(r.last_checked_on):
        return "Open"
    if pd.notna(r.last_seen_date):
        return f"Listed · seen {pd.Timestamp(r.last_seen_date):%d %b}"
    return "Not checked"


display["status"] = display.apply(_status, axis=1)
display["cities"] = display["cities"].apply(lambda c: ", ".join(c) if c else "Not stated")
display["skills"] = display["skills"].apply(lambda s: ", ".join(s[:6]) + (f" +{len(s)-6} more" if len(s) > 6 else ""))

cols = ["status", "title", "company", "role_family", "experience", "cities", "working_type",
        "salary", "skills", "posted_date", "source", "url"]

event = st.dataframe(
    display[cols],
    use_container_width=True,
    hide_index=True,
    height=min(600, 60 + 36 * len(display)),
    column_config={
        "url": st.column_config.LinkColumn("Listing", display_text="Open ↗"),
        "source": "Board",
        "status": "Status",
        "role_family": "Role",
        "working_type": "Arrangement",
        "posted_date": "Posted",
    },
    on_select="rerun",
    selection_mode="single-row",
)

st.caption("Click a row to see the full description.")


# Every match, not just the page on screen -- a CSV of 25 rows labelled as
# "the results" would be quietly incomplete. Fetched only on request, since
# it is up to one call per 200 postings; kept until the filters change.
def _all_matches() -> pd.DataFrame:
    frames, page_no = [], 1
    while True:
        chunk, chunk_meta = dc.search_postings(page=page_no, page_size=200, sort_by=sort_by,
                                               order=order, **filters)
        frames.append(chunk)
        if page_no >= (chunk_meta.get("pages") or 1):
            break
        page_no += 1
    out = pd.concat(frames, ignore_index=True)
    for col in out.columns:
        if out[col].map(lambda v: isinstance(v, list)).any():
            out[col] = out[col].map(lambda v: "; ".join(map(str, v)) if isinstance(v, list) else v)
    return out


if st.session_state.get("jobs_csv_signature") != signature:
    st.session_state.pop("jobs_csv", None)
if st.button(f"Prepare CSV of all {total} matching postings"):
    st.session_state["jobs_csv"] = _all_matches()
    st.session_state["jobs_csv_signature"] = signature
dc.csv_download(st.session_state.get("jobs_csv"), f"jobs_{date.today()}.csv",
                f"Download {total} postings (CSV)")


# =====================================================================
# DETAIL — only fetched for the row actually selected, since the
# description is heavy and most rows are never opened.
# =====================================================================
selected_rows = event.selection.rows if event and event.selection else []
if selected_rows:
    job_id = int(items.iloc[selected_rows[0]]["job_id"])
    detail = dc.posting_detail(job_id)

    with st.container(border=True):
        company_line = detail.get("company") or "Unknown company"
        if detail.get("company_rating") is not None:
            reviews = detail.get("company_reviews")
            reviews_text = f", {reviews:,} reviews" if reviews else ""
            company_line += f" (★ {detail['company_rating']}{reviews_text})"
        st.subheader(f"{detail.get('title') or 'Untitled'} — {company_line}")

        if detail.get("is_expired"):
            st.error(
                f"**Naukri has closed this posting** — confirmed "
                f"{detail.get('expired_on')}. That is the date we checked and "
                "found it gone, not necessarily the day it closed, and it does "
                "not tell us whether anyone was hired."
            )
        elif detail.get("is_expired") is False and detail.get("last_checked_on"):
            st.success(f"Still listed as of {detail['last_checked_on']}.")
        elif not detail.get("last_checked_on"):
            board = ("hirist never says when a posting closes"
                     if detail.get("source") == "hirist"
                     else "it has not been checked for closure yet")
            st.info(f"**Not verified open** — {board}. Our searches last saw it "
                    f"on {detail.get('last_seen_date') or 'an unknown date'}.")

        # Says "may" and names the evidence on purpose. Roughly half of these
        # are a recruiter writing a different office into the body, so the
        # reader has to be able to judge it rather than take a verdict.
        flagged_cities = detail.get("description_foreign_cities") or []
        if flagged_cities:
            own = ", ".join(detail.get("cities") or []) or "not stated"
            st.warning(
                f"**This description may belong to a different posting.** It "
                f"mentions {', '.join(flagged_cities)} and none of this posting's "
                f"own locations ({own}). Everything else here — company, title, "
                "experience, dates — is read separately and is unaffected. Skills "
                "found in the description text may not be this job's."
            )

        if detail.get("company_badges"):
            st.caption(" · ".join(detail["company_badges"]))

        applicants_display = "—"
        if detail.get("applicant_count") is not None:
            qualifier = detail.get("applicant_count_qualifier")
            if qualifier == "at_least":
                applicants_display = f"{detail['applicant_count']}+"
            elif qualifier == "less_than":
                applicants_display = f"<{detail['applicant_count']}"
            else:
                applicants_display = str(detail["applicant_count"])

        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Experience", display.iloc[selected_rows[0]]["experience"])
        d2.metric("Salary", display.iloc[selected_rows[0]]["salary"])
        d3.metric("Applicants", applicants_display)
        d4.metric("Times seen", detail.get("times_seen") or 1)
        d5.metric("Days listed", detail.get("days_listed") if detail.get("days_listed") is not None else "—")

        extra_facts = [detail.get(f) for f in ("seniority_level", "role_category", "naukri_role", "department", "industry_type") if detail.get(f)]
        if extra_facts:
            st.caption(" · ".join(extra_facts))

        if detail.get("skills"):
            preferred = set(detail.get("preferred_skills") or [])
            star = lambda s: f"⭐ {s}" if s in preferred else s

            # Skills the posting treats as interchangeable are shown as
            # their own "any one of" line rather than mixed in with the
            # rest — listing them together is what made a posting look
            # like it wanted AWS *and* Azure *and* GCP.
            choices = detail.get("skill_choices") or []
            in_a_choice = {s for group in choices for s in group}
            required = [s for s in detail["skills"] if s not in in_a_choice]

            if required:
                st.markdown("**Skills required:** " + ", ".join(star(s) for s in required))
            for group in choices:
                st.markdown("**Any one of:** " + " / ".join(star(s) for s in group))
            if choices:
                st.caption("Alternatives are detected from the description text — best effort, "
                           "so treat them as a hint rather than the final word.")
            if preferred:
                st.caption("⭐ = marked as a preferred skill by the employer")

        if detail.get("certifications"):
            st.markdown("**Certifications mentioned:** " + ", ".join(detail["certifications"]))

        if detail.get("qualification_degrees"):
            # Grouped by level, one degree per entry -- e.g. Naukri's
            # "B.Tech / B.E." shows as two separate degrees here, each
            # only with the specializations that actually pair with it.
            by_level: dict[str, list[str]] = {}
            for d in detail["qualification_degrees"]:
                label = d["degree"]
                if d["specializations"]:
                    label += f" ({', '.join(d['specializations'])})"
                by_level.setdefault(d["level"], []).append(label)
            level_order = ["UG", "PG", "Doctorate"]
            parts = [
                f"{level}: {', '.join(by_level[level])}"
                for level in level_order if level in by_level
            ]
            st.markdown("**Education:** " + " · ".join(parts))
        elif detail.get("qualifications"):
            quals = ", ".join(f"{q['level']}: {q['field_of_study']}" for q in detail["qualifications"])
            st.markdown("**Education:** " + quals)

        # Responsibilities/Requirements are a best-effort split of the same
        # description text below — shown when the split actually found
        # something, since a posting phrased unusually just won't have it.
        if detail.get("responsibilities_text"):
            st.markdown("**Responsibilities**")
            st.write(detail["responsibilities_text"])
        if detail.get("requirements_text"):
            st.markdown("**Requirements**")
            st.write(detail["requirements_text"])

        with st.expander("Full description"):
            st.write(detail.get("description") or "No description available.")

        if detail.get("url"):
            # Was hardcoded to "Open on Naukri", which mislabelled every
            # posting from any other board.
            board = detail.get("source")
            label = {"naukri": "Open on Naukri",
                     "hirist": "Open on hirist"}.get(board, "Open listing")
            st.link_button(label, detail["url"])

dc.sampling_note()
