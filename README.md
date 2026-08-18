# Job Market Intelligence

Structured data on Indian IT job postings, collected from Naukri.com and turned into a queryable API and an analytics dashboard: what skills employers are actually asking for, how demand shifts day to day, and how individual postings compare.

Postings come from a fixed set of Naukri searches and cities, sampled daily — the numbers describe that sample, not the Indian IT market as a whole. That caveat is surfaced throughout the dashboard itself, not just here.

## Architecture

```
Naukri.com → Playwright scraper → Python cleaning layer (in-process)
           → PostgreSQL (cleaned_postings + sub-tables) → FastAPI → Streamlit dashboard
```

- **Collection** (`naukri_collector.py`, `skill_taxonomy.py`) — Playwright scraper, visible browser (Naukri blocks headless). Discovers job URLs from a search page, then reads each posting's fields directly off labelled DOM elements — no AI, no guessing: title, company, experience, location, key skills, employment type, work mode, salary, posted date, openings, description, and Education (UG/PG/Doctorate, whichever levels a posting actually shows). A regex-based skill taxonomy mines the description text for tools/frameworks Naukri's own tags missed.
- **Cleaning layer** (`cleaning.py`) — plain Python, not SQL, and not a separate batch step. `clean_record()` takes one scraped posting and returns everything needed to write it: skill name normalization, experience/salary range parsing, work-mode/employment/contract-type detection, city resolution, role classification, and qualification-level normalization.
- **Storage** (`job_database.py`) — calls `clean_record()` on every scraped posting and writes straight into `cleaned_postings`, `posting_qualifications`, and `posting_cities`. Skills go through one more step: each normalized skill name is resolved against a `skills` dictionary table (one row per distinct skill, its own `skill_id`), auto-registering any name not seen before. `posting_skills` stores one row per *posting* — `skill_ids INT[]`, GIN-indexed — rather than one row per skill, trading away Postgres's ability to index into the array for aggregation (skill demand, co-occurrence, and the daily snapshot all `unnest()` it at query time) for meaningfully less storage and a simpler single-posting lookup. A skill's category (Frontend/Backend/Database/Cloud-DevOps/Data-ML/Testing/Languages) is seeded from a lookup dict in `cleaning.py` only at the moment it's first registered; after that it lives purely in the `skills` table, so correcting one later is a data edit, not a code change. There is no raw/staging table — nothing scraped is ever stored unprocessed. Dedup is by a fingerprint of company + title + location + experience; a repeat sighting refreshes every field to its latest known value (salary, description, URL — postings do get edited after they go live) while preserving `first_seen_date`. `snapshot_daily_skills()` (SQL, in `trends_setup.sql`) freezes one skill-count-per-day afterward, since `cleaned_postings` itself only ever shows the present.
- **API** (`api/`) — FastAPI + Pydantic + a psycopg2 connection pool, no ORM. Four routers: `postings` (filtered/paginated search), `reference` (canonical lookups for filter UIs), `analytics` (aggregates), `trends` (time series, movers). Every query is parameterised. Interactive docs at `/docs`.
- **Dashboard** (`Home.py`, `pages/`, `dash_common.py`) — Streamlit multipage app, Plotly charts. `dash_common.py` is the only file that talks HTTP; every page calls named wrapper functions there, which hit the API and cache results for 5 minutes. Pages have no knowledge of the database schema.
- **Automation** (`run_daily_scrape.bat`, `start_demo.bat`, Windows Task Scheduler) — daily scrape (cleans and writes as it goes) → snapshot, logged per run. `start_demo.bat` brings up the API and waits for a real health check before starting the dashboard, so nothing races.

## Project structure

```
naukri_collector.py      scraper — discovery + detail extraction
skill_taxonomy.py        regex skill vocabulary used by the scraper
cleaning.py               cleaning layer — one scraped record in, a cleaned record out
job_database.py            writes cleaned_postings + posting_skills/qualifications/cities
schema.sql                   every table shape — cities/states, cleaned_postings, skills dictionary, posting_cities/skills/qualifications
trends_setup.sql                daily snapshot + trend views
api/
  main.py               FastAPI app, lifespan-managed connection pool
  database.py           pooled cursor helpers, WhereBuilder
  models.py             Pydantic response models
  routers/              postings, reference, analytics, trends
Home.py                 dashboard landing page
pages/
  1_Skills.py           demand, pairings, seniority split
  2_Market.py           roles, employers, locations, work arrangement
  3_Trends.py           demand over time, movers, new arrivals
  4_Jobs.py             individual posting search
dash_common.py           shared API client for every dashboard page
run_daily_scrape.bat     scheduled scrape (cleans in-process) → snapshot
start_demo.bat           health-checked launcher: API, then dashboard
```

## Setup

**Prerequisites:** Python 3.13, PostgreSQL, and Chromium via Playwright.

```
pip install -r requirements.txt
pip install -r api/requirements.txt
playwright install chromium
```

**Database** — run once, in this order:

```
psql -U postgres -d jobmarket -f schema.sql
psql -U postgres -d jobmarket -f trends_setup.sql
```

**Configuration** — connection settings come from environment variables, never from a file:

```
PGDATABASE   (default: jobmarket)
PGUSER       (default: postgres)
PGPASSWORD
PGHOST       (default: localhost)
PGPORT       (default: 5432)
```

The API additionally accepts a single `DATABASE_URL`, and the dashboard accepts `API_BASE_URL` (default: `http://localhost:8000`).

## Running it

```
uvicorn api.main:app --reload      # in one terminal
streamlit run Home.py              # in another
```

Or, on Windows, `start_demo.bat` does both — refreshes data, starts the API, waits for a real health check, then starts the dashboard.

To scrape on demand:

```
python naukri_collector.py "https://www.naukri.com/software-engineer-jobs-in-hyderabad" --limit 20
```

Each run cleans and writes as it scrapes — no separate cleaning step. Run `SELECT snapshot_daily_skills();` afterward to fold that run into the trend history — `run_daily_scrape.bat` does this automatically for the scheduled searches.
