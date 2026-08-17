"""
Naukri job collector — discovery + detail scraping, pure DOM extraction.

Two stages:
  1. DISCOVERY — load a Naukri search results page, collect job URLs.
  2. DETAIL — visit each job URL, scrape the required parameters.

No taxonomy matching, no AI. Every value below is read directly from a
labelled element on the page. If a label isn't there, the field reports
"not found" rather than being guessed at.

Parameters collected:
    Job title, Company name, Experience, Location,
    Key Skills, Employment Type, Description

Naukri blocks headless browsers, so this runs with a visible window.
ONE window is opened for the whole run and reused for every posting —
it is not one window per job.

Setup:
    pip install playwright
    playwright install chromium

Usage:
    python naukri_collector.py "https://www.naukri.com/software-engineer-jobs-in-hyderabad"
    python naukri_collector.py "<search url>" --limit 5
"""

import re
import sys
import time
import json
import random
from datetime import date, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from skill_taxonomy import extract_skills
from job_database import save_records


def parse_posted_date(raw: str | None) -> date | None:
    """Turn Naukri's relative text into an actual calendar date.

    'Today' / 'Just now' / '5 hours ago'  -> today
    '1 day ago' / '3 days ago'            -> today minus N days
    '30+ days ago'                        -> None

    That last one is deliberate: '30+' could mean 31 days or 300, so
    inventing a specific date would be fabricating precision that isn't
    in the source. The raw text is stored alongside either way, so
    nothing is lost."""
    if not raw:
        return None

    text = raw.strip().lower()

    if "+" in text:  # "30+ days ago" — genuinely unknown, don't guess
        return None

    if any(w in text for w in ["today", "just now", "hour", "minute", "moment"]):
        return date.today()

    match = re.search(r"(\d+)\s*day", text)
    if match:
        return date.today() - timedelta(days=int(match.group(1)))

    match = re.search(r"(\d+)\s*month", text)
    if match:
        return date.today() - timedelta(days=int(match.group(1)) * 30)

    return None  # unrecognised phrasing — better than a wrong date


NOT_FOUND = "not found"

# Seconds to pause between detail page loads. Randomised so the request
# pattern doesn't look perfectly mechanical.
THROTTLE_MIN = 3
THROTTLE_MAX = 6


def safe_text(scope, selector: str) -> str | None:
    """Read a labelled element's text, or return None if it isn't there.
    Never raises — a missing field shouldn't kill the whole run."""
    try:
        el = scope.query_selector(selector)
        return el.inner_text().strip() if el else None
    except Exception:
        return None


def safe_texts(scope, selector: str) -> list[str]:
    """Same idea, for a list of elements (e.g. skill chips)."""
    try:
        els = scope.query_selector_all(selector)
        return [e.inner_text().strip() for e in els if e.inner_text().strip()]
    except Exception:
        return []


def safe_education(page) -> dict[str, str]:
    """Naukri's Education block lists UG/PG/Doctorate as label:value rows
    under styles_education__KXFkO — but not every posting shows all
    three (a posting with no doctorate requirement often omits that row
    entirely rather than showing 'not required'), so this reads
    whatever rows are actually present instead of assuming a fixed set.
    Never raises — same contract as safe_text/safe_texts."""
    try:
        rows = page.query_selector_all("div.styles_education__KXFkO div.styles_details__Y424J")
        education = {}
        for row in rows:
            label_el = row.query_selector("label")
            span_el = row.query_selector("span")
            if not label_el or not span_el:
                continue
            key = label_el.inner_text().strip().rstrip(":").strip()
            value = span_el.inner_text().strip()
            if key and value:
                education[key] = value
        return education
    except Exception:
        return {}


# ---------------------------------------------------------------------
# STAGE 1 — DISCOVERY
# Collect job URLs from a search results page.
# ---------------------------------------------------------------------
def discover_job_urls(page, search_url: str, limit: int | None = None) -> list[str]:
    print(f"\n[discovery] Loading search results: {search_url}")
    page.goto(search_url, wait_until="domcontentloaded", timeout=45000)

    try:
        page.wait_for_selector("div.srp-jobtuple-wrapper", timeout=20000)
    except PlaywrightTimeoutError:
        print("[discovery] No job cards appeared — page may be blocked or the layout changed.")
        return []

    cards = page.query_selector_all("div.srp-jobtuple-wrapper")
    print(f"[discovery] Found {len(cards)} job cards on this page.")

    urls = []
    for card in cards:
        link = card.query_selector("a.title")
        if not link:
            continue
        href = link.get_attribute("href")
        if href and href not in urls:
            urls.append(href)

    if limit:
        urls = urls[:limit]
    print(f"[discovery] Collected {len(urls)} job URLs.")
    return urls


# ---------------------------------------------------------------------
# STAGE 2 — DETAIL
# Visit one job page and read the required parameters off it.
# ---------------------------------------------------------------------
def scrape_job_detail(page, url: str) -> dict | None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)

    try:
        page.wait_for_selector("div.styles_JDC__dang-inner-html__h0K4t", timeout=20000)
    except PlaywrightTimeoutError:
        print(f"  [skip] Description never rendered — {url}")
        return None

    # --- Key Skills: Naukri's own tagged chips ---
    # Primary: the dedicated Key Skills section on the detail page.
    skills = safe_texts(page, "div.styles_key-skill__GIPn_ a span")
    # Fallback: some layouts render the chips without the anchor wrapper.
    if not skills:
        skills = safe_texts(page, "div.styles_key-skill__GIPn_ span")

    # --- Employment Type and Role Category: sibling rows in the same
    # labelled "other details" block, same class, same nested-span
    # markup — just different label text. (Department and Industry
    # Type used to be scraped from this same pattern too, but their
    # actual markup doesn't match it — it was capturing a stray comma
    # instead of real text — and they weren't needed, so removed
    # rather than chasing the right selector for them.) ---
    employment_type = safe_text(
        page, "div.styles_details__Y424J:has-text('Employment Type') span span"
    )
    role_category = safe_text(
        page, "div.styles_details__Y424J:has-text('Role Category') span span"
    )

    # --- Education: UG/PG/Doctorate, wherever those rows actually appear ---
    education = safe_education(page)

    # --- Working type: Naukri's separate work-mode badge (Hybrid/Remote/WFO) ---
    working_type = safe_text(page, ".styles_jhc__wfhmode__iQwF4 span")

    description = safe_text(page, "div.styles_JDC__dang-inner-html__h0K4t")

    # --- Technologies named in the description body ---
    # Naukri's chips are often sparse (3 tags on a posting naming a dozen
    # tools), so scan the description text too. This field shows ONLY what
    # the chips missed — anything already tagged above is filtered out, so
    # the two fields together give full coverage with no repetition.
    # Compared case-insensitively, since Naukri writes "Power Bi" where the
    # taxonomy returns "Power BI".
    all_tech = extract_skills(description) if description else []
    already_tagged = {s.lower() for s in skills}
    tech_in_description = [t for t in all_tech if t.lower() not in already_tagged]

    # --- Posted date ---
    # The class is shared with other stats on the same row (Openings,
    # Applicants), so scope by the "Posted" label rather than the class
    # alone — same disambiguation problem we hit on LinkedIn.
    posted_raw = safe_text(page, "span.styles_jhc__stat__PgY67:has-text('Posted') span")
    posted_date = parse_posted_date(posted_raw)

    # --- Openings ---
    # The employer states this directly, so it beats inferring vacancy
    # count from duplicate listings.
    openings_raw = safe_text(page, "span.styles_jhc__stat__PgY67:has-text('Openings') span")
    openings = None
    if openings_raw:
        digits = re.search(r"\d+", openings_raw)
        openings = int(digits.group()) if digits else None

    record = {
        "url": url,
        "title": safe_text(page, "h1.styles_jd-header-title__rZwM1") or NOT_FOUND,
        "company": safe_text(page, "div.styles_jd-header-comp-name__MvqAI a") or NOT_FOUND,
        "experience": safe_text(page, ".styles_jhc__exp__k_giM span") or NOT_FOUND,
        "location": safe_text(page, ".styles_jhc__loc___Du2H .styles_jhc__location__W_pVs") or NOT_FOUND,
        "key_skills": skills or NOT_FOUND,
        "employment_type": employment_type or NOT_FOUND,
        "working_type": working_type or NOT_FOUND,
        "salary": safe_text(page, ".styles_jhc__salary__jdfEC span") or NOT_FOUND,
        "posted_date": posted_date.isoformat() if posted_date else NOT_FOUND,
        "posted_raw": posted_raw or NOT_FOUND,
        "openings": openings if openings is not None else NOT_FOUND,
        "description": description or NOT_FOUND,
        "role_category": role_category or NOT_FOUND,
    }

    # Only include these when something was actually found — an empty
    # "not found" line, or an empty dict, adds noise without information.
    if tech_in_description:
        record["tech_in_description"] = tech_in_description
    if education:
        record["education"] = education

    return record


def print_record(record: dict, index: int, total: int):
    print(f"\n--- Job {index}/{total} ---")
    for field in ["title", "company", "experience", "location",
                  "key_skills", "tech_in_description", "employment_type",
                  "role_category", "education",
                  "working_type", "salary", "posted_date", "openings"]:
        if field not in record:  # omitted fields stay omitted in the output too
            continue
        value = record[field]
        if isinstance(value, list):
            value = ", ".join(value)
        elif isinstance(value, dict):
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        print(f"{field}: {value}")


def main(search_url: str, limit: int | None):
    with sync_playwright() as p:
        # ONE visible browser for the entire run — Naukri blocks headless.
        # Every job below reuses this same window; it is not reopened.
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        urls = discover_job_urls(page, search_url, limit)
        if not urls:
            browser.close()
            return

        records = []
        for i, url in enumerate(urls, start=1):
            print(f"\n[detail {i}/{len(urls)}] {url[:90]}...")
            record = scrape_job_detail(page, url)
            if record:
                records.append(record)
                print_record(record, i, len(urls))

            # Throttle between requests — slower, but hammering the site
            # is the fastest way to get the whole run blocked.
            if i < len(urls):
                pause = random.uniform(THROTTLE_MIN, THROTTLE_MAX)
                print(f"  (pausing {pause:.1f}s)")
                time.sleep(pause)

        browser.close()

        # Write to Postgres. Postings already in the table are not
        # duplicated — their last_seen_date is refreshed instead.
        try:
            new_count, repeat_count = save_records(records)
            print(f"\nDatabase: {new_count} new, {repeat_count} already known.")
        except Exception as e:
            print(f"\nDatabase write failed: {e}")
            print("Falling back to JSON so this run isn't lost.")
            with open("naukri_jobs.json", "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

        print(f"Scraped {len(records)} of {len(urls)} jobs.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python naukri_collector.py "<naukri search url>" [--limit N]')
        sys.exit(1)

    url_arg = sys.argv[1]
    limit_arg = None
    if "--limit" in sys.argv:
        try:
            limit_arg = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit needs a number, e.g. --limit 5")
            sys.exit(1)

    main(url_arg, limit_arg)
