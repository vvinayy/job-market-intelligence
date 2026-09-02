"""
hirist job collector — discovery in a browser, detail from hirist's own JSON.

Two stages, deliberately on different transports:

  1. DISCOVERY — render a hirist search page and harvest job codes. The
     listings are built client-side, so a plain request returns navigation and
     SEO boilerplate with no jobs in it. Measured: 162KB of HTML containing
     zero experience ranges, against 3.7KB of rendered text containing twenty.

  2. DETAIL — read each posting from `gladiator.hirist.tech/job/detail`, the
     JSON endpoint the page itself calls. Measured 39/39 with a plain HTTP
     client, so this path needs no browser, no DOM selectors, and nothing that
     breaks when hirist restyles. That matters here: the page's own class names
     are generated (`mui-style-15bl3sl`), which is the same trap that has
     broken the Naukri selectors twice.

This emits the SAME raw-record shape `naukri_collector` produces, so
`clean_record()` and `save_records()` consume it unchanged and every column,
endpoint and chart downstream needs no edit. Source is told apart later by
`url`, which is populated on every row ever written.

What hirist does not publish, and is therefore left out rather than invented:
education, employment type, contract type, openings, industry, company review
counts and badges. `functionalArea` arrives as a bare integer with no lookup
shipped, so it is skipped too — storing "25" as a department name would be
worse than storing nothing.

Usage:
    python hirist_collector.py "https://www.hirist.tech/search/python-jobs-in-hyderabad"
    python hirist_collector.py "<search url>" --limit 5
"""

import html
import random
import re
import sys
import time
from datetime import datetime, date

import httpx
from playwright.sync_api import sync_playwright

from skill_taxonomy import extract_skills
from job_database import (save_records, record_scrape_run, check_field_health,
                          snapshot_daily_skills)

NOT_FOUND = "not found"

DETAIL_API = "https://gladiator.hirist.tech/job/detail"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Search pages get the scraper's usual 3-6s: they are full renders pulling
# subresources. Detail calls get 1-2s because each is a single JSON request
# with no page to build — the same distinction liveness_checker.py makes, and
# for the same measured reason.
SEARCH_THROTTLE = (3, 6)
DETAIL_THROTTLE = (1.0, 2.0)

JOB_CODE_RE = re.compile(r"/j/[^/?#]*?-(\d{6,})")


# =====================================================================
# HTML → TEXT
# =====================================================================
def strip_html(raw: str | None) -> str | None:
    """hirist ships the description as HTML; Naukri's arrives as rendered
    text. Levelling that here keeps the difference out of cleaning.py.

    Block tags become newlines before tags are removed, so the section
    structure split_description_sections() looks for survives — and so
    find_skill_choice_groups(), which parses sentence punctuation, is not
    handed markup."""
    if not raw:
        return None
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


# =====================================================================
# STAGE 1 — DISCOVERY
# =====================================================================
def discover_job_codes(page, search_url: str, limit: int | None = None) -> list[str]:
    """Job codes from one rendered search page, in the order listed.

    Codes, not URLs: the detail API is keyed by code, and the slug in the
    href is decoration that can change without the posting changing."""
    print(f"\n[discovery] Loading search results: {search_url}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    # Nothing to wait for by selector — the cards mount after hydration, and
    # a fixed settle is more honest than guessing at a generated class name.
    page.wait_for_timeout(7000)

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    codes = list(dict.fromkeys(JOB_CODE_RE.findall(" ".join(hrefs))))
    print(f"[discovery] Found {len(codes)} job codes on this page.")

    if limit:
        codes = codes[:limit]
    return codes


# =====================================================================
# STAGE 2 — DETAIL
# =====================================================================
def fetch_job(client: httpx.Client, code: str) -> dict | None:
    """One posting's JSON, or None if hirist did not answer with one.

    Returns None rather than raising for the same reason safe_text() does in
    the Naukri collector: one unavailable posting must cost one posting, not
    the run."""
    try:
        response = client.get(DETAIL_API, params={"jobcode": code})
    except Exception as exc:
        print(f"  [skip] {code}: {type(exc).__name__}")
        return None
    if response.status_code != 200:
        print(f"  [skip] {code}: HTTP {response.status_code}")
        return None
    try:
        data = response.json().get("data")
    except ValueError:
        print(f"  [skip] {code}: response was not JSON")
        return None
    if not isinstance(data, dict):
        print(f"  [skip] {code}: no job object in response")
        return None
    return data


# =====================================================================
# MAPPING — pure, no I/O, so it is testable without the network
# =====================================================================
def _experience_text(data: dict) -> str | None:
    """hirist gives min/max as integers. They are rendered back into the
    string form cleaning.py already parses, so one parser serves both
    sources — and so the fingerprint sees a comparable shape rather than a
    format unique to this collector."""
    lo, hi = data.get("min"), data.get("max")
    if lo is None and hi is None:
        return None
    if hi is None:
        return f"{lo} years"
    return f"{lo} - {hi} years"


def _salary_text(data: dict) -> str | None:
    """Only when a figure was actually disclosed.

    `hideSal` is set on 33 of 39 sampled postings, and minSal/maxSal are 0
    there. Reporting 0 would state a salary nobody offered — the same mistake
    normalize_working_type() made on 372 rows."""
    if data.get("hideSal"):
        return None
    lo, hi = data.get("minSal") or 0, data.get("maxSal") or 0
    if not lo and not hi:
        return None
    return f"{lo}-{hi} LPA"


def _posted(data: dict) -> tuple[date | None, str | None]:
    """(date, raw). createdTime is epoch milliseconds, so unlike Naukri's
    '30+ days ago' there is nothing here that has to become NULL for honesty
    — the exact day is known."""
    ms = data.get("createdTime")
    if not ms:
        return None, None
    try:
        day = datetime.fromtimestamp(int(ms) / 1000).date()
    except (TypeError, ValueError, OSError):
        return None, None
    return day, day.isoformat()


def _working_type(data: dict) -> str | None:
    """Remote when flagged, otherwise NOTHING.

    Absence of the flag means hirist was not told, not that the role is
    on-site — only 3 of 39 sampled postings carry it. Reading silence as
    "On-site" is exactly the fabrication that put invented values on 372
    Naukri rows."""
    return "Remote" if data.get("workFromHome") else None


def build_record(data: dict, search_url: str | None = None) -> dict:
    """One hirist API payload → one raw record in the Naukri collector's
    shape. Fields hirist does not publish are omitted, never defaulted."""
    tags = [t for t in (data.get("tags") or []) if isinstance(t, dict)]
    key_skills = [t["name"] for t in tags if t.get("name")]

    # isMandatory is deliberately NOT mapped to preferred_key_skills. That
    # column records Naukri's starred chips -- a "preferred" marker the
    # employer applies. hirist publishes "mandatory", a different statement,
    # and how much of a posting it covers varies by employer (3 of 7, 3 of 17,
    # 4 of 12 across three sampled postings). Reading one as the other would
    # put two different facts in one column. Every tag still lands in
    # key_skills, so no skill is lost by leaving this out.

    locations = [l["name"] for l in (data.get("locations") or [])
                 if isinstance(l, dict) and l.get("name")]

    description = strip_html(data.get("introText"))
    posted_date, posted_raw = _posted(data)

    # Same rule as the Naukri collector: report only what the chips missed,
    # so the two fields together cover everything without repeating.
    already_tagged = {s.lower() for s in key_skills}
    tech_in_description = [t for t in (extract_skills(description) if description else [])
                           if t.lower() not in already_tagged]

    company = (data.get("companyData") or {}).get("companyName")

    record = {
        "title": data.get("title") or NOT_FOUND,
        "company": company or NOT_FOUND,
        "url": data.get("jobDetailUrl") or NOT_FOUND,
        "experience": _experience_text(data) or NOT_FOUND,
        "location": ", ".join(locations) or NOT_FOUND,
        "key_skills": key_skills,
        "salary": _salary_text(data) or NOT_FOUND,
        "working_type": _working_type(data) or NOT_FOUND,
        "posted_date": posted_date,
        "posted_raw": posted_raw or NOT_FOUND,
        "description": description or NOT_FOUND,
        "applicant_count": data.get("applyCount") if data.get("applyCount") is not None else NOT_FOUND,
        "company_rating": data.get("minRatingAb") or NOT_FOUND,
        "source_search": search_url or NOT_FOUND,
    }

    # Added only when present, matching the Naukri collector — an empty
    # "not found" entry adds noise without information.
    if tech_in_description:
        record["tech_in_description"] = tech_in_description

    return record


def print_record(record: dict, index: int, total: int):
    print(f"\n--- Job {index}/{total} ---")
    for field in ["title", "company", "experience", "location", "key_skills",
                  "preferred_key_skills", "tech_in_description", "working_type",
                  "salary", "posted_date", "applicant_count", "company_rating"]:
        if field not in record:
            continue
        value = record[field]
        if isinstance(value, list):
            value = ", ".join(value)
        print(f"{field}: {value}")


# =====================================================================
# RUN
# =====================================================================
def compute_field_found_counts(records: list[dict]) -> dict[str, int]:
    """How many records actually found each field — the basis for
    check_field_health()'s per-field rate, same as the Naukri collector."""
    counts: dict[str, int] = {}
    for record in records:
        for key, value in record.items():
            if value in (NOT_FOUND, None) or value in ([], {}):
                continue
            counts[key] = counts.get(key, 0) + 1
    return counts


def main(search_url: str, limit: int | None = None):
    started_at = datetime.now()

    with sync_playwright() as p:
        # A browser only for discovery. Detail calls below need none, which is
        # why this window closes as soon as the codes are collected.
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            codes = discover_job_codes(page, search_url, limit)
        finally:
            browser.close()

    if not codes:
        # Zero codes is itself a signal — the layout may have changed, or the
        # search returned nothing. Logged like any other run, not skipped.
        _log_run(search_url, started_at, datetime.now(), 0, 0, 0, {}, True, None)
        return

    records = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30,
                      follow_redirects=True) as client:
        for i, code in enumerate(codes, start=1):
            print(f"\n[detail {i}/{len(codes)}] jobcode={code}")
            data = fetch_job(client, code)
            if data:
                record = build_record(data, search_url)
                records.append(record)
                print_record(record, i, len(codes))
            if i < len(codes):
                pause = random.uniform(*DETAIL_THROTTLE)
                print(f"  (pausing {pause:.1f}s)")
                time.sleep(pause)

    storage_ok, error_message = True, None
    new_count = repeat_count = 0
    try:
        new_count, repeat_count = save_records(records)
        print(f"\nDatabase: {new_count} new, {repeat_count} already known.")
    except Exception as e:
        storage_ok, error_message = False, str(e)
        print(f"\nDatabase write failed: {e}")

    finished_at = datetime.now()
    _log_run(search_url, started_at, finished_at, len(codes), len(records),
             new_count + repeat_count, compute_field_found_counts(records),
             storage_ok, error_message)

    if storage_ok:
        _snapshot_today()

    duration = (finished_at - started_at).total_seconds()
    print(f"\nRun took {duration:.0f}s. Scraped {len(records)} of {len(codes)} jobs.")


def _log_run(search_url, started_at, finished_at, postings_found, postings_scraped,
             postings_written, field_counts, storage_ok, error_message):
    """Health tracking failing must never make a successful scrape look
    failed — caught and reported, not raised."""
    try:
        run_id = record_scrape_run(
            search_url, started_at, finished_at, postings_found,
            postings_scraped, postings_written, field_counts, storage_ok, error_message,
        )
        for w in check_field_health(run_id):
            print(f"\n[HEALTH WARNING] {w['field']}: {w['current_rate']:.0%} this run "
                  f"vs {w['historical_avg_rate']:.0%} historical average")
        if not storage_ok:
            print(f"\n[HEALTH WARNING] Storage failure this run: {error_message}")
    except Exception as e:
        print(f"\n[health tracking failed, not fatal] {e}")


def _snapshot_today():
    """Records today's skill counts, per source. Deliberately loud on
    failure: a day not snapshotted cannot be recovered afterwards."""
    try:
        written = snapshot_daily_skills()
        print(f"[snapshot] {written} skill-day rows recorded.")
    except Exception as e:
        print(f"\n[SNAPSHOT FAILED — today's trend data is lost unless rerun] {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    cap = None
    if "--limit" in sys.argv:
        cap = int(sys.argv[sys.argv.index("--limit") + 1])
    main(url, cap)
