# Job Market Intelligence

Structured data on Indian IT job postings, collected from Naukri.com and turned into a queryable API and an analytics dashboard: what skills employers are actually asking for, how demand shifts day to day, and how individual postings compare.

Postings come from a fixed set of Naukri searches and cities, sampled daily — the numbers describe that sample, not the Indian IT market as a whole. That caveat is surfaced throughout the dashboard itself, not just here.

## Architecture

```
Naukri.com → Playwright scraper → PostgreSQL (raw) → Python cleaning layer
           → PostgreSQL (cleaned) → FastAPI → Streamlit dashboard
```

- **Collection** (`naukri_collector.py`, `skill_taxonomy.py`) — Playwright scraper, visible browser (Naukri blocks headless). Discovers job URLs from a search page, then reads each posting's fields directly off labelled DOM elements — no AI, no guessing. A regex-based skill taxonomy mines the description text for tools/frameworks Naukri's own tags missed.
- **Storage** (`job_database.py`) — writes to `raw_postings`, deduped by a fingerprint of company + title + location + experience. A repeat sighting refreshes every field to its latest known value (salary, description, URL — postings do get edited after they go live) while preserving `first_seen_date`.
- **Cleaning layer** (`cleaning.py`) — plain Python, not SQL. Reads `raw_postings`, computes every derived field (skill normalization, experience/salary range parsing, work-mode/employment/contract-type detection, city resolution, role classification), writes `cleaned_postings` and `posting_cities`. Postgres stores; Python decides. `schema.sql` and `schema_v2.sql` only define table shapes now — the transformation logic that used to live in PL/pgSQL functions moved here. `snapshot_daily_skills()` (still SQL, in `trends_setup.sql`) freezes one skill-count-per-day afterward, since `cleaned_postings` itself is overwritten on every run.
- **API** (`api/`) — FastAPI + Pydantic + a psycopg2 connection pool, no ORM. Four routers: `postings` (filtered/paginated search), `reference` (canonical lookups for filter UIs), `analytics` (aggregates), `trends` (time series, movers). Every query is parameterised. Interactive docs at `/docs`.
- **Dashboard** (`Home.py`, `pages/`, `dash_common.py`) — Streamlit multipage app, Plotly charts. `dash_common.py` is the only file that talks HTTP; every page calls named wrapper functions there, which hit the API and cache results for 5 minutes. Pages have no knowledge of the database schema.
- **Automation** (`run_daily_scrape.bat`, `start_demo.bat`, Windows Task Scheduler) — daily scrape → clean → snapshot, logged per run. `start_demo.bat` brings up the API and waits for a real health check before starting the dashboard, so nothing races.

## Project structure

```
naukri_collector.py      scraper — discovery + detail extraction
skill_taxonomy.py        regex skill vocabulary used by the scraper
job_database.py          scraper's DB layer — fingerprinting, upsert
cleaning.py               cleaning layer — raw_postings -> cleaned_postings, in Python
schema.sql                 base schema — raw_postings
schema_v2.sql                table shapes only — cities/states, cleaned_postings, posting_cities
trends_setup.sql              daily snapshot + trend views
api/
  main.py               FastAPI app, lifespan-managed connection pool
  database.py           pooled cursor helpers, WhereBuilder
  models.py             Pydantic response models
  routers/              postings, reference, analytics, trends
Home.py                 dashboard landing page
pages/
  1_Skills.py           demand, pairings, seniority split, network view
  2_Market.py           roles, employers, locations, work arrangement
  3_Trends.py           demand over time, movers, new arrivals
  4_Jobs.py             individual posting search
dash_common.py           shared API client for every dashboard page
run_daily_scrape.bat     scheduled scrape → clean → snapshot
start_demo.bat           health-checked launcher: API, then dashboard
```

## Setup

**Prerequisites:** Python 3.13, PostgreSQL, and Chromium via Playwright.

```
pip install -r requirements.txt
pip install -r api/requirements.txt
playwright install chromium
```

**Database** — run once, in this exact order (later scripts assume earlier ones already ran):

```
psql -U postgres -d jobmarket -f schema.sql
psql -U postgres -d jobmarket -f schema_v2.sql
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

Then run `python cleaning.py` and `SELECT snapshot_daily_skills();` to bring the cleaned tables and trend history up to date — `run_daily_scrape.bat` does this automatically for the scheduled searches.
