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
    Key Skills (with preferred/starred subset), Employment Type, Role Category,
    Role, Industry Type, Department, Applicant count, Company rating/reviews,
    Company badges, Description

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
import shutil
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from skill_taxonomy import extract_skills
from job_database import save_records, record_scrape_run, check_field_health


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


def parse_count_with_suffix(raw: str | None) -> int | None:
    """'50.5K Reviews' -> 50500, '1.2M' -> 1200000, '850' -> 850.
    Approximate by nature — Naukri's own figure is already rounded, this
    just expands the K/M shorthand rather than adding any real precision."""
    if not raw:
        return None
    match = re.search(r"([\d.]+)\s*([KkMm]?)", raw)
    if not match:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000}.get(match.group(2).lower(), 1)
    return int(float(match.group(1)) * multiplier)


def parse_applicant_count(raw: str | None) -> tuple[int | None, str | None]:
    """Naukri shows this three different ways, confirmed by sampling
    real postings: a plain exact number ('44'), a floor once past some
    threshold ('100+', meaning at least that many), or a ceiling for
    very new postings ('Less than 10'). A single digit-extraction regex
    treats all three the same, which is actually wrong for the last
    one — '10' extracted from 'Less than 10' means the true count is
    BELOW 10, not at least 10, the opposite direction from the '+' case.

    Returns (count, qualifier): qualifier is None for a plain exact
    number, 'at_least' for the '+' case, 'less_than' for the ceiling
    case — so the ambiguity direction is preserved instead of silently
    collapsed into a bare int that looks exact either way."""
    if not raw:
        return None, None
    match = re.search(r"\d+", raw)
    if not match:
        return None, None
    count = int(match.group())
    if "+" in raw:
        return count, "at_least"
    if "less than" in raw.lower():
        return count, "less_than"
    return count, None


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


def safe_text_by_exact_label(page, label: str) -> str | None:
    """Like safe_text, but matches a details row by its <label>'s EXACT
    text rather than has-text() substring matching. Needed because
    Naukri has label pairs where one is a substring of the other —
    "Role:" vs "Role Category:" — so :has-text('Role') would match
    both rows and there'd be no way to tell them apart. Reads the
    value from an <a> tag if there is one (Role, like Industry Type
    and Department, wraps its value in a link), falling back to a
    plain nested span otherwise."""
    try:
        for row in page.query_selector_all("div.styles_details__Y424J"):
            label_el = row.query_selector("label")
            if not label_el or label_el.inner_text().strip() != label:
                continue
            value_el = row.query_selector("span a") or row.query_selector("span span")
            return value_el.inner_text().strip() if value_el else None
    except Exception:
        pass
    return None


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
def scrape_job_detail(page, url: str, search_url: str | None = None) -> dict | None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)

    try:
        page.wait_for_selector("div.styles_JDC__dang-inner-html__h0K4t", timeout=20000)
    except PlaywrightTimeoutError:
        print(f"  [skip] Description never rendered — {url}")
        return None

    # --- Key Skills: Naukri's own tagged chips, some starred as
    # "preferred" (confirmed from the page's own legend: "Skills
    # highlighted with [star] are preferred keyskills" — the star is an
    # <i class="ni-icon-jd-save"> inside the chip). Walking the chip
    # elements directly, rather than safe_texts() on just the spans,
    # since knowing which chip a name came from is what makes the
    # preferred/not-preferred split possible. ---
    skills = []
    preferred_key_skills = []
    for chip in page.query_selector_all("div.styles_key-skill__GIPn_ a"):
        span = chip.query_selector("span")
        text = (span.inner_text() if span else chip.inner_text()).strip()
        if not text:
            continue
        skills.append(text)
        if chip.query_selector("i.ni-icon-jd-save"):
            preferred_key_skills.append(text)
    # Fallback: some layouts render the chips without the anchor wrapper
    # — preferred-skill info isn't recoverable in that shape.
    if not skills:
        skills = safe_texts(page, "div.styles_key-skill__GIPn_ span")

    # --- Company rating/reviews: shown inline in the header, sourced
    # from AmbitionBox — no separate company-page visit needed. ---
    company_rating = safe_text(page, ".styles_amb-rating__4UyFL")
    company_reviews_raw = safe_text(page, ".styles_amb-reviews__0J1e3")

    # --- Company recognition badges: also inline, in the "About the
    # company" block ("Fortune India 500 (2023)", "Highly Rated by
    # Women", etc). A couple of these render as short, terse single
    # words ("TOP") — confirmed from raw HTML that this is genuinely
    # what Naukri shows, not a truncation artifact of the extraction. ---
    company_badges = safe_texts(page, ".styles_company-info-tags__y6RDs .styles_chips__AKDM0")

    # --- Employment Type and Role Category: sibling rows in the same
    # labelled "other details" block. Their value sits directly in a
    # nested span with no anchor tag, so span span reaches it. ---
    employment_type = safe_text(
        page, "div.styles_details__Y424J:has-text('Employment Type') span span"
    )
    role_category = safe_text(
        page, "div.styles_details__Y424J:has-text('Role Category') span span"
    )

    # --- Role: Naukri's own classification (e.g. "Back End Developer"),
    # distinct from Role Category (e.g. "Software Development") and from
    # our own regex-derived role_family. :has-text('Role') would also
    # match the "Role Category:" row since it contains "Role" as a
    # substring, so this needs the exact-label helper instead. Same
    # anchor-wrapped markup as Industry Type/Department (confirmed from
    # real page HTML), which safe_text_by_exact_label already handles.
    naukri_role = safe_text_by_exact_label(page, "Role:")

    # --- Industry Type and Department: same "other details" block, but
    # different internal markup from Employment Type/Role Category — the
    # value sits inside an <a> tag, with a trailing decorative comma in
    # its own sibling span (<a>value</a><span class="...comma...">,</span>).
    # span span (the pattern that works above) matches that comma span
    # instead of the anchor — confirmed from real page markup — which is
    # exactly why these two were broken and removed earlier. Targeting
    # the anchor directly instead, and reading all of them since the
    # comma implies more than one value is possible.
    industry_type = ", ".join(safe_texts(
        page, "div.styles_details__Y424J:has-text('Industry Type') span a"
    )) or None
    department = ", ".join(safe_texts(
        page, "div.styles_details__Y424J:has-text('Department') span a"
    )) or None

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

    # --- Applicants ---
    # Naukri shows this three ways ("44", "100+", "Less than 10") —
    # parse_applicant_count() keeps the direction of the ambiguity
    # rather than collapsing all three into a bare number.
    applicants_raw = safe_text(page, "span.styles_jhc__stat__PgY67:has-text('Applicants') span")
    applicant_count, applicant_count_qualifier = parse_applicant_count(applicants_raw)

    company_reviews = parse_count_with_suffix(company_reviews_raw)

    record = {
        "url": url,
        "title": safe_text(page, "h1.styles_jd-header-title__rZwM1") or NOT_FOUND,
        "company": safe_text(page, "div.styles_jd-header-comp-name__MvqAI a") or NOT_FOUND,
        "experience": safe_text(page, ".styles_jhc__exp__k_giM span") or NOT_FOUND,
        "location": safe_text(page, ".styles_jhc__loc___Du2H .styles_jhc__location__W_pVs") or NOT_FOUND,
        "key_skills": skills or NOT_FOUND,
        "employment_type": employment_type or NOT_FOUND,
        "role_category": role_category or NOT_FOUND,
        "naukri_role": naukri_role or NOT_FOUND,
        "industry_type": industry_type or NOT_FOUND,
        "department": department or NOT_FOUND,
        "working_type": working_type or NOT_FOUND,
        "salary": safe_text(page, ".styles_jhc__salary__jdfEC span") or NOT_FOUND,
        "posted_date": posted_date.isoformat() if posted_date else NOT_FOUND,
        "posted_raw": posted_raw or NOT_FOUND,
        "openings": openings if openings is not None else NOT_FOUND,
        "applicant_count": applicant_count if applicant_count is not None else NOT_FOUND,
        "company_rating": company_rating or NOT_FOUND,
        "company_reviews": company_reviews if company_reviews is not None else NOT_FOUND,
        "description": description or NOT_FOUND,
        "source_search": search_url or NOT_FOUND,
    }

    # Only include these when something was actually found — an empty
    # "not found" line, or an empty dict, adds noise without information.
    if tech_in_description:
        record["tech_in_description"] = tech_in_description
    if education:
        record["education"] = education
    if preferred_key_skills:
        record["preferred_key_skills"] = preferred_key_skills
    if company_badges:
        record["company_badges"] = company_badges
    # None here is a real, meaningful value ("exact count, no qualifier
    # needed") distinct from "not found" — so this key is only added at
    # all when there's an actual qualifier to record, rather than using
    # the NOT_FOUND-sentinel pattern above, which would conflate the two.
    if applicant_count_qualifier:
        record["applicant_count_qualifier"] = applicant_count_qualifier

    return record


def print_record(record: dict, index: int, total: int):
    print(f"\n--- Job {index}/{total} ---")
    for field in ["title", "company", "experience", "location",
                  "key_skills", "preferred_key_skills", "tech_in_description", "employment_type",
                  "role_category", "naukri_role", "industry_type", "department", "education",
                  "working_type", "salary", "posted_date", "openings",
                  "applicant_count", "applicant_count_qualifier",
                  "company_rating", "company_reviews", "company_badges"]:
        if field not in record:  # omitted fields stay omitted in the output too
            continue
        value = record[field]
        if isinstance(value, list):
            value = ", ".join(value)
        elif isinstance(value, dict):
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        print(f"{field}: {value}")


# =====================================================================
# HEALTH TRACKING — makes a broken selector, a slow run, or a storage
# failure show up in the run's own log instead of only being caught
# later by someone happening to notice, which is how Department/
# Industry Type and the applicant-count bug were actually found.
# =====================================================================
def compute_field_found_counts(records: list[dict]) -> dict[str, int]:
    """How many records actually found each field (not NOT_FOUND, not
    empty) — the basis for check_field_health()'s per-field rate."""
    counts: dict[str, int] = {}
    for record in records:
        for key, value in record.items():
            if value in (NOT_FOUND, None) or value in ([], {}):
                continue
            counts[key] = counts.get(key, 0) + 1
    return counts


def check_disk_space(path: str = ".", min_free_gb: float = 1.0) -> str | None:
    """A warning string if free space is critically low, else None —
    catches 'storage is full' before it causes a write to fail
    partway through a run rather than after."""
    free_gb = shutil.disk_usage(path).free / (1024 ** 3)
    if free_gb < min_free_gb:
        return f"Low disk space: {free_gb:.2f} GB free (threshold {min_free_gb} GB)"
    return None


def main(search_url: str, limit: int | None):
    started_at = datetime.now()
    disk_warning = check_disk_space()
    if disk_warning:
        print(f"  [WARNING] {disk_warning}")

    with sync_playwright() as p:
        # ONE visible browser for the entire run — Naukri blocks headless.
        # Every job below reuses this same window; it is not reopened.
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        urls = discover_job_urls(page, search_url, limit)
        if not urls:
            browser.close()
            # Zero URLs from discovery is itself a real health signal —
            # could mean the search layout changed or got blocked, not
            # necessarily that there's nothing to find. Logged the same
            # as any other run rather than silently returning.
            _log_run(search_url, started_at, datetime.now(), 0, 0, 0, {}, True, None, disk_warning)
            return

        records = []
        for i, url in enumerate(urls, start=1):
            print(f"\n[detail {i}/{len(urls)}] {url[:90]}...")
            record = scrape_job_detail(page, url, search_url)
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
        storage_ok, error_message = True, None
        new_count = repeat_count = 0
        try:
            new_count, repeat_count = save_records(records)
            print(f"\nDatabase: {new_count} new, {repeat_count} already known.")
        except Exception as e:
            storage_ok, error_message = False, str(e)
            print(f"\nDatabase write failed: {e}")
            print("Falling back to JSON so this run isn't lost.")
            with open("naukri_jobs.json", "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

        finished_at = datetime.now()
        field_counts = compute_field_found_counts(records)
        _log_run(search_url, started_at, finished_at, len(urls), len(records),
                  new_count + repeat_count, field_counts, storage_ok, error_message, disk_warning)

        duration = (finished_at - started_at).total_seconds()
        print(f"\nRun took {duration:.0f}s. Scraped {len(records)} of {len(urls)} jobs.")


def _log_run(search_url, started_at, finished_at, postings_found, postings_scraped,
             postings_written, field_counts, storage_ok, error_message, disk_warning):
    """Records this run and prints anything that looks wrong. Health
    tracking failing is never allowed to make an otherwise-successful
    scrape look like it failed — caught and reported, not raised."""
    try:
        run_id = record_scrape_run(
            search_url, started_at, finished_at, postings_found,
            postings_scraped, postings_written, field_counts, storage_ok, error_message,
        )
        warnings = check_field_health(run_id)
        if warnings:
            print("\n[HEALTH WARNING] These fields dropped sharply vs recent runs — a selector may be broken:")
            for w in warnings:
                print(f"  - {w['field']}: {w['current_rate']:.0%} this run vs "
                      f"{w['historical_avg_rate']:.0%} historical average")
        if disk_warning:
            print(f"\n[HEALTH WARNING] {disk_warning}")
        if not storage_ok:
            print(f"\n[HEALTH WARNING] Storage failure this run: {error_message}")
    except Exception as e:
        print(f"\n[health tracking failed, not fatal] {e}")


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
